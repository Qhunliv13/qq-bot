#!/usr/bin/env pwsh
# 第二层整理：清掉 local-her\napcat 下明显重复/可重新下载的中间物（2026-10-25）
# 保留：shell（正在用的 NapCat）、QQ933（便携 QQ）、NapCat.52230.Shell（另一份 QQ 包）
$ErrorActionPreference = "Continue"
$base = "C:\qhunliv13_web\local-her\napcat"
Set-Location $base

$before = (Get-ChildItem $base -Recurse -File -EA SilentlyContinue |
    Measure-Object Length -Sum).Sum
$report = @()

# ① 顶层重复压缩包（内容已解压到 shell/ 等目录）
foreach ($n in @("shell.zip", "NapCat.Shell.zip", "OneKey.zip", "framework.zip")) {
    $p = Join-Path $base $n
    if (Test-Path $p) {
        $sz = (Get-Item $p).Length
        Remove-Item $p -Force
        $report += "  删除重复压缩包 {0,-22} {1,7:N1} MB" -f $n, ($sz / 1MB)
    }
}

# ② 顶层 QQ.exe（300MB，是 QQ933 里的副本）
$exe = Join-Path $base "QQ.exe"
if (Test-Path $exe) {
    $sz = (Get-Item $exe).Length
    Remove-Item $exe -Force
    $report += "  删除重复 QQ.exe                   {0,7:N1} MB" -f ($sz / 1MB)
}

# ③ QQ933 里的缓存目录（可再生）
$caches = @("QQ933\Files\cache", "QQ933\Files\Cache", "qqbot-profile\Cache",
            "qqbot-profile\Code Cache", "qqbot-profile\GPUCache",
            "qqbot-profile\blob_storage")
foreach ($c in $caches) {
    $p = Join-Path $base $c
    if (Test-Path $p) {
        $sz = (Get-ChildItem $p -Recurse -File -EA SilentlyContinue |
            Measure-Object Length -Sum).Sum
        Remove-Item $p -Recurse -Force -EA SilentlyContinue
        $report += "  清缓存 {0,-30} {1,7:N1} MB" -f $c, ($sz / 1MB)
    }
}

$after = (Get-ChildItem $base -Recurse -File -EA SilentlyContinue |
    Measure-Object Length -Sum).Sum
$report += ""
$report += "  napcat 目录: {0:N1} MB → {1:N1} MB（释放 {2:N1} MB）" -f (
    $before / 1MB), ($after / 1MB), (($before - $after) / 1MB)
$report | ForEach-Object { Write-Output $_ }
