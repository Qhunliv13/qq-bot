# Native Windows entry point: all bot/runtime behavior belongs to native_runtime.py.
# Compatible with Windows PowerShell 5.1; no WSL, Docker, elevation or persistent policy changes.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('wizard', 'init', 'up', 'doctor', 'info', 'status', 'stop', 'logs', 'health', 'chat')]
    [string] $Command = 'wizard',
    [string] $ModelBase,
    [string] $Model,
    [string] $ApiKeyEnv,
    [string] $PersonaFile,
    [string] $DataDir,
    [string] $RuntimeName,
    [ValidateRange(1, 3600)]
    [int] $WaitTimeout,
    [switch] $NonInteractive,
    [switch] $NoBrowser,
    [switch] $ShowPassword,
    [switch] $Json,
    [string[]] $OwnerQQ,
    # Read chat input through stdin only, never a command-line argument or saved transcript.
    [Parameter(ValueFromPipeline = $true, DontShow = $true)]
    [string] $InputLine
)
begin {
    $hasPipelineInput = $MyInvocation.ExpectingInput
    $pipelineInput = @()
}
process {
    if ($hasPipelineInput) { $pipelineInput += $InputLine }
}
end {
function Assert-NativeArgument {
    <# Reject controls and quotes before crossing PowerShell 5.1 native argv quoting boundaries. #>
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f\x7f"`]' -or $Value.EndsWith('\')) {
        # A trailing backslash before a closing quote is ambiguous in legacy native invocation.
        throw 'Invalid native argument.'
    }
}

function Get-NativeVenvVersion {
    <# Read only a bounded version hint; Python remains the authority for ownership and venv validity. #>
    param([string] $Directory)
    $marker = Join-Path $Directory '.native-bootstrap.json'
    $configuration = Join-Path $Directory 'runtime\.venv\pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or -not (Test-Path -LiteralPath $configuration -PathType Leaf)) {
        # Foreign/Lite state never supplies interpreter paths or a version preference.
        return $null
    }
    $reader = [IO.File]::OpenText($configuration)
    try {
        # Never use home/executable/command from this untrusted file, or read unbounded metadata.
        $buffer = New-Object char[] 4097
        $length = $reader.ReadBlock($buffer, 0, $buffer.Length)
        if ($length -eq 0 -or $length -gt 4096) { throw 'Invalid venv metadata.' }
        $text = -join $buffer[0..($length - 1)]
        $versions = [regex]::Matches($text, '(?m)^version[ \t]*=[^\r\n]*')
        if ($versions.Count -ne 1 -or $versions[0].Value -cnotmatch '^version[ \t]*=[ \t]*(3\.(?:12|13))\.[0-9]+[ \t]*$') {
            throw 'Unsupported venv metadata.'
        }
        return $Matches[1]
    } finally { $reader.Dispose() }
}

function Invoke-NativePythonProbe {
    <# Probe only stdlib with private NUL stdin and an atomic, bounded process-tree owner; never run runtime here. #>
    # Five seconds per child tolerates local antivirus/startup delay; lazy Add-Type runs once outside this budget.
    param([string] $Executable, [string[]] $Prefix, [int] $TimeoutMilliseconds = 5000)
    if (-not ('Qhunliv.NativePythonProbe' -as [type])) {
        # No Python is needed to supervise discovery. Windows 10+ JOB_LIST prevents the create/assign
        # orphan race, including py.exe and venv redirectors; HANDLE_LIST never passes chat handles.
        # Win32 constants/structures follow processthreadsapi.h, winbase.h and jobapi2.h, matching
        # native_setup_process.py's atomic ownership contract without requiring a discovered Python.
        Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
namespace Qhunliv {
    public static class NativePythonProbe {
        [StructLayout(LayoutKind.Sequential)] struct Security {
            public int Length; public IntPtr Descriptor; public int Inherit;
        }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup {
            public int Size; public IntPtr Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCount, YCount, Fill, Flags;
            public short Show, ReservedSize; public IntPtr ReservedBytes, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct StartupEx {
            public Startup Info; public IntPtr Attributes;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {
            public IntPtr Process, Thread; public int ProcessId, ThreadId;
        }
        [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr Minimum, Maximum; public uint Active;
            public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct Limits {
            public BasicLimit Basic;
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        [StructLayout(LayoutKind.Sequential)] struct Accounting {
            public long User, Kernel, PeriodUser, PeriodKernel;
            public uint Faults, Total, Active, Terminated;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateJobObjectW(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetInformationJobObject(IntPtr job, int kind, ref Limits limits, int length);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateFileW(string path, uint access, uint share, ref Security security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool CreateProcessW(string application, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity,
            bool inherit, uint flags, IntPtr environment, string directory, ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting info, int length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        static void Check(bool success) {
            // Fixed errors never disclose interpreter paths, environment values or input.
            if (!success) throw new InvalidOperationException("Native Python probe failed.");
        }
        static bool Empty(IntPtr job, IntPtr process) {
            // A root exit alone cannot certify that a launcher descendant has stopped.
            Accounting info;
            Check(QueryInformationJobObject(job, 1, out info, Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero));
            uint state = WaitForSingleObject(process, 0);
            Check(state == 0 || state == 258);
            return state == 0 && info.Active == 0;
        }
        static bool Cleanup(IntPtr job, IntPtr process) {
            // Cleanup has a separate short bound; uncertain cleanup must stop discovery, not retry.
            Stopwatch clock = Stopwatch.StartNew();
            do {
                TerminateJobObject(job, 1);
                try { if (Empty(job, process)) return true; }
                catch (InvalidOperationException) { }
                Thread.Sleep(10);
            } while (clock.ElapsedMilliseconds < 1000);
            return false;
        }
        public static int Run(string executable, string selector, int milliseconds) {
            // Only resolved executables and fixed selectors reach this private helper.
            if (!Path.IsPathRooted(executable) || executable.IndexOfAny(new char[] {'"', '\0', '\r', '\n'}) >= 0 ||
                (selector != "" && selector != "-3.12" && selector != "-3.13") || milliseconds < 1 || milliseconds > 5000) return -1;
            IntPtr job = IntPtr.Zero, nul = IntPtr.Zero, attributes = IntPtr.Zero, jobs = IntPtr.Zero, handles = IntPtr.Zero;
            ProcessInfo process = new ProcessInfo();
            bool initialized = false;
            int result = -1;
            Stopwatch clock = Stopwatch.StartNew();
            try {
                job = CreateJobObjectW(IntPtr.Zero, null); Check(job != IntPtr.Zero);
                Limits limits = new Limits(); limits.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE; non-inheritable job handle.
                Check(SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(typeof(Limits))));
                Security security = new Security(); security.Length = Marshal.SizeOf(typeof(Security)); security.Inherit = 1;
                nul = CreateFileW("NUL", 0xC0000000, 3, ref security, 3, 0, IntPtr.Zero);
                Check(nul != IntPtr.Zero && nul != new IntPtr(-1));
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size); Check(size != IntPtr.Zero);
                attributes = Marshal.AllocHGlobal(size);
                Check(InitializeProcThreadAttributeList(attributes, 2, 0, ref size)); initialized = true;
                jobs = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(jobs, job);
                handles = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(handles, nul);
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x2000D), jobs, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero));
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero));
                StartupEx startup = new StartupEx(); startup.Info.Size = Marshal.SizeOf(typeof(StartupEx));
                startup.Info.Flags = 0x100; startup.Info.Input = nul; startup.Info.Output = nul; startup.Info.Error = nul;
                startup.Attributes = attributes;
                // Exit codes encode the two supported minors, avoiding unbounded capture pipes entirely.
                string code = "import sys; sys.exit(300 + sys.version_info.minor if sys.version_info.major == 3 and sys.version_info.minor in (12, 13) else 1)";
                StringBuilder command = new StringBuilder("\"" + executable + "\" " + selector + " -I -S -B -c \"" + code + "\"");
                Check(CreateProcessW(executable, command, IntPtr.Zero, IntPtr.Zero, true, 0x08080000,
                    IntPtr.Zero, null, ref startup, out process));
                while (clock.ElapsedMilliseconds < milliseconds) {
                    if (Empty(job, process.Process)) {
                        uint exit; Check(GetExitCodeProcess(process.Process, out exit));
                        if (clock.ElapsedMilliseconds < milliseconds && (exit == 312 || exit == 313)) result = (int)exit;
                        break;
                    }
                    Thread.Sleep(10);
                }
            } catch (Exception) {
                // Unsupported job APIs fail closed: never fall back to an uncontained process.
                result = -1;
            } finally {
                // Even unexpected teardown exceptions cannot skip handle closure or authorize a retry.
                if (process.Process != IntPtr.Zero) {
                    try { if (!Cleanup(job, process.Process)) result = -2; }
                    catch (Exception) { result = -2; }
                }
                if (initialized) DeleteProcThreadAttributeList(attributes);
                foreach (IntPtr memory in new IntPtr[] {handles, jobs, attributes}) if (memory != IntPtr.Zero) Marshal.FreeHGlobal(memory);
                foreach (IntPtr handle in new IntPtr[] {process.Thread, process.Process, nul, job})
                    if (handle != IntPtr.Zero && handle != new IntPtr(-1)) CloseHandle(handle);
            }
            return result;
        }
    }
}
'@ -ErrorAction Stop
    }
    $selector = if ($Prefix.Count) { [string] $Prefix[0] } else { '' }
    $result = [Qhunliv.NativePythonProbe]::Run($Executable, $selector, $TimeoutMilliseconds)
    if ($result -eq -2) { throw 'Native Python probe cleanup could not be confirmed.' }
    if ($result -in @(312, 313)) { return '3.' + ($result - 300) }
    return $null
}

function Get-NativePython {
    <# Prefer 3.12 for new installs; existing venvs require a matching supported system interpreter. #>
    param([string] $RequiredVersion = '')
    $minors = if ($RequiredVersion) { @($RequiredVersion) } else { @('3.12', '3.13') }
    $candidates = @()
    foreach ($minor in $minors) {
        $candidates += @{ Name = 'py.exe'; Prefix = @('-' + $minor) }
    }
    $candidates += @(
        @{ Name = 'python.exe'; Prefix = @() },
        @{ Name = 'python3.exe'; Prefix = @() }
    )
    if ($env:LOCALAPPDATA) {
        # winget installs may not update this running process's PATH; do not mutate user/machine PATH.
        foreach ($minor in $minors) {
            $directoryMinor = $minor.Replace('.', '')
            $candidates += @{ Name = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$directoryMinor\python.exe"); Prefix = @() }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            $resolved = Get-Command $candidate.Name -ErrorAction SilentlyContinue
            if (-not $resolved) { continue }
            $executable = if ($resolved.Source) { [string] $resolved.Source } else { [string] $resolved.Name }
            # Store aliases can open an interactive installer; they are not existing interpreters.
            if ($executable -match '(?i)[\\/]Microsoft[\\/]WindowsApps[\\/]') { continue }
            $prefix = @($candidate.Prefix)
        } catch {
            # Resolution failures may contain executable paths; continue without printing them.
            continue
        }
        # One isolated probe is shared by every candidate. Cleanup uncertainty propagates to the
        # entry failure handler, so it cannot start another candidate or authorize an installation.
        $version = Invoke-NativePythonProbe $executable $prefix
        if ($version -cmatch '^3\.(12|13)$') {
            # Selector names and PATH may resolve to a different minor; the probe is authoritative.
            if ($RequiredVersion -and $version -cne $RequiredVersion) { continue }
            return @{ Executable = $executable; Prefix = $prefix }
        }
    }
    return $null
}

function Confirm-NativePythonInstall {
    <# Require a deliberate user choice before a user-scope package installation; never request admin rights. #>
    $message = 'Python 3.12 or 3.13 was not found. Install official Python 3.12 for your Windows account with winget? No administrator access is requested.'
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
        # Default No prevents an accidental Enter key from authorizing installation.
        $choice = [System.Windows.MessageBox]::Show($message, 'QhunlivBot native setup', 'YesNo', 'Question', 'No')
        return ([string] $choice -eq 'Yes')
    } catch {
        # Headless hosts may lack desktop assemblies. A closed/redirected console is not consent.
        if ([Console]::IsInputRedirected) { return $false }
        try { return ((Read-Host ($message + ' [y/N]')) -cmatch '^[yY]([eE][sS])?$') }
        catch { return $false }
    }
}

function Install-NativePython {
    <# Invoke only the official exact winget package, user scope, after the caller obtained explicit consent. #>
    param([string] $Winget)
    $global:LASTEXITCODE = $null
    & $Winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
}

function Get-NativeSetupBrowserUrl {
    <# Validate the loopback capability; only an explicit NoBrowser choice authorizes terminal display. #>
    param([string] $Line)
    $match = [regex]::Match($Line, '\ABOT_SETUP_URL=(http://127\.0\.0\.1:([0-9]{1,5})/#token=[A-Za-z0-9_-]{32,})\z')
    if (-not $match.Success) { return $null }
    $port = [int] $match.Groups[2].Value
    if ($port -lt 1 -or $port -gt 65535) { return $null }
    return $match.Groups[1].Value
}

# Native launcher entry point. Fixed failures never echo raw errors, paths, keys or capabilities.
$failureHint = 'Invalid option. Use a supported native command and Windows paths; keys belong in the setup form or a named environment variable.'
# PowerShell 5.1 encodes native stdin with OutputEncoding and decodes native stdout with Console.OutputEncoding.
# Preserve both per invocation; never modify a profile, environment variable, registry or persistent code page.
$previousOutputEncoding = $OutputEncoding
$previousConsoleOutputEncoding = $null
$consoleOutputEncodingChanged = $false
try {
    if ($ShowPassword -and $Command -ne 'info') { throw 'ShowPassword requires info.' }
    if ($Json -and $Command -ne 'info') { throw 'Json requires info.' }
    if ($ShowPassword -and $Json) { throw 'ShowPassword cannot be combined with Json.' }
    if ($NoBrowser -and $Command -ne 'wizard') { throw 'NoBrowser requires wizard.' }
    # A constructor expression avoids PSObject wrapping: the PS5.1 native writer must receive an Encoding instance.
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = $utf8
    try {
        $previousConsoleOutputEncoding = [Console]::OutputEncoding
        [Console]::OutputEncoding = $utf8
        $consoleOutputEncodingChanged = $true
    } catch [System.IO.IOException] {
        # A host without a console can reject SetConsoleOutputCP; do not attach/allocate a console as a side effect.
        # Its redirected handles remain host-owned; continue without exposing the exception or changing host state.
    }
    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    $entry = Join-Path $repo 'tools\native_runtime.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        $failureHint = 'The native runtime entry point is missing. Restore the complete checkout and retry.'
        throw 'Missing native runtime.'
    }
    # Python owns runtime/config semantics. This layer only validates transport and forwards typed flags.
    $runtimeArgs = @($Command)
    foreach ($pair in @(@('ModelBase', '--model-base'), @('Model', '--model'), @('ApiKeyEnv', '--api-key-env'), @('PersonaFile', '--persona-file'), @('RuntimeName', '--runtime-name'))) {
        if ($PSBoundParameters.ContainsKey($pair[0])) {
            $value = [string] $PSBoundParameters[$pair[0]]
            Assert-NativeArgument $value
            if ($pair[0] -eq 'ApiKeyEnv' -and $value -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid environment variable name.' }
            $runtimeArgs += @($pair[1], $value)
        }
    }
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        Assert-NativeArgument $DataDir
    } else {
        # Full never shares the Lite directory or writes mutable state inside the checkout.
        if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable.' }
        $DataDir = Join-Path $env:LOCALAPPDATA 'QhunlivBot\Full'
    }
    if ($DataDir -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])') { throw 'DataDir must be a Windows absolute path.' }
    $runtimeArgs += @('--data-dir', $DataDir)
    if ($PSBoundParameters.ContainsKey('WaitTimeout')) { $runtimeArgs += @('--wait-timeout', [string] $WaitTimeout) }
    if ($NonInteractive) { $runtimeArgs += '--non-interactive' }
    if ($ShowPassword) { $runtimeArgs += '--show-password' }
    if ($Json) { $runtimeArgs += '--json' }
    foreach ($owner in $OwnerQQ) {
        if ($owner -cnotmatch '^[0-9]{5,20}$') { throw 'Invalid OwnerQQ.' }
        $runtimeArgs += @('--owner-qq', $owner)
    }
    # Request the private transport marker explicitly; Python must not open a second browser.
    if ($Command -eq 'wizard') { $runtimeArgs += @('--no-browser', '--print-setup-url') }

    $failureHint = 'Existing native state could not be inspected. Restore the original private data directory; no files were changed.'
    # Existing/foreign/incomplete state must never trigger an automatic Python installation.
    $existingState = (Test-Path -LiteralPath $DataDir) -and (@(Get-ChildItem -LiteralPath $DataDir -Force -ErrorAction Stop | Select-Object -First 1).Count -gt 0)
    $requiredVersion = Get-NativeVenvVersion $DataDir
    $failureHint = if ($requiredVersion) {
        'The existing native runtime requires its matching Python 3.12 or 3.13 base installation. Restore the original Python version for your account and retry; the private venv was not changed.'
    } else {
        'Python 3.12 or 3.13 is required. Install Python 3.12 for your account, then retry; no WSL or Docker is needed.'
    }
    $python = Get-NativePython $requiredVersion
    if (-not $python -and -not $existingState -and -not $NonInteractive -and $Command -in @('wizard', 'init', 'up')) {
        $wingetCommand = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
        if ($wingetCommand -and (Confirm-NativePythonInstall)) {
            $winget = if ($wingetCommand.Source) { [string] $wingetCommand.Source } else { [string] $wingetCommand.Name }
            $failureHint = 'Python installation did not finish. Retry setup, or install official Python 3.12 for your account. Administrator access is not required.'
            Install-NativePython $winget
            $python = Get-NativePython
        }
    }
    if (-not $python) { throw 'Supported Python not found.' }
    $executable = [string] $python.Executable
    $launchArgs = @($python.Prefix) + @('-u', $entry) + $runtimeArgs
    $failureHint = 'Native runtime launch failed. Run doctor with the same DataDir; keep API keys in the setup form or environment, not command arguments.'
    $global:LASTEXITCODE = $null
    if ($Command -eq 'wizard') {
        # Stream a single redirected stdout pipe. Stderr stays inherited, so neither pipe can fill while waiting.
        $setupAddressHandled = $false
        & $executable @launchArgs | ForEach-Object {
            $line = [string] $_
            if ($line.Contains('BOT_SETUP_URL=')) {
                $setupUrl = Get-NativeSetupBrowserUrl $line
                if (-not $setupUrl) {
                    [Console]::Error.WriteLine('Setup returned an invalid local browser address; no browser was opened.')
                } elseif (-not $setupAddressHandled) {
                    # A repeated marker cannot open extra tabs or duplicate a manually displayed capability.
                    $setupAddressHandled = $true
                    if ($NoBrowser) {
                        # Explicit NoBrowser is the user's authorization to display this local-only capability.
                        Write-Host 'Manual setup: open the following address only on this computer. Do not share it or copy it to public logs.'
                        Write-Output $setupUrl
                    } else {
                        try {
                            Start-Process -FilePath $setupUrl -ErrorAction Stop | Out-Null
                            Write-Host 'The local setup page is open. Keep this window open while using it.'
                        } catch {
                            # Browser errors may contain the URL: retain a fixed message and require deliberate disclosure.
                            [Console]::Error.WriteLine('The setup browser could not open. Close setup, then run wizard -NoBrowser to display a private manual setup address on this computer. Do not share that address.')
                        }
                    }
                }
                # Default, malformed and repeated capability markers never reach routine terminal output.
            } else { Write-Output $line }
        }
    } elseif ($hasPipelineInput) { $pipelineInput | & $executable @launchArgs }
    else { & $executable @launchArgs }
    if ($null -eq $LASTEXITCODE) { throw 'Native runtime did not return an exit code.' }
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Windows native launcher: ' + $failureHint)
    exit 2
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($consoleOutputEncodingChanged) {
        try { [Console]::OutputEncoding = $previousConsoleOutputEncoding }
        catch [System.IO.IOException] {
            # A detached/closed console cannot be repaired here without a new global host side effect.
        }
    }
}
} # end: preserve a live console or forward pipeline input exactly once
