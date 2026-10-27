#!/usr/bin/env pwsh
# 整理工作区（2026-10-25）：只清理测试临时目录/派生物/旧日志/旧截图，不碰源码与数据
$ErrorActionPreference = "Continue"
$root = "C:\qhunliv13_web"
Set-Location $root

function SizeOf([string]$p) {
    if (-not (Test-Path $p)) { return 0 }
    (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
}

$before = SizeOf $root
$report = @()

# ① pytest / 临时目录（可安全删除）
$tmpDirs = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like ".pytest-tmp-*" -or $_.Name -like ".pt-*" -or
    $_.Name -eq ".testtmp" -or $_.Name -eq ".pytest_cache" -or
    $_.Name -eq "__pycache__" -or $_.Name -like "_deploy_artifacts_*" -or
    $_.Name -like "_dsh_sess*" -or $_.Name -like "_diag_ab" -or
    $_.Name -like "_probe_*" -or $_.Name -like "_lbwork" -or
    $_.Name -like "_lbgo" -or $_.Name -like "_lb_ref"
}
foreach ($d in $tmpDirs) {
    $sz = SizeOf $d.FullName
    Remove-Item $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
    $report += "  删除临时目录 {0,-30} {1,8:N1} MB" -f $d.Name, ($sz / 1MB)
}

# ② 会话历史的大副本（原 zip 仍在 Downloads，可随时重解）
$big = Join-Path $root "local-her\session-restore\session.v4.jsonl"
if (Test-Path $big) {
    $sz = (Get-Item $big).Length
    Remove-Item $big -Force
    $report += "  删除历史副本 session.v4.jsonl        {0,8:N1} MB" -f ($sz / 1MB)
}

# ③ 不再需要的安装包（155 上已有副本）
foreach ($f in @("local-her\linuxqq.deb", "local-her\lagrange.tar.gz")) {
    $p = Join-Path $root $f
    if (Test-Path $p) {
        $sz = (Get-Item $p).Length
        Remove-Item $p -Force
        $report += "  删除安装包 {0,-28} {1,8:N1} MB" -f (Split-Path $f -Leaf), ($sz / 1MB)
    }
}

# ④ 日志归拢
$logDir = Join-Path $root "local-her\_logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$moved = 0
Get-ChildItem (Join-Path $root "local-her") -File -Filter "run*.log" -ErrorAction SilentlyContinue |
    ForEach-Object { Move-Item $_.FullName $logDir -Force; $moved++ }
Get-ChildItem (Join-Path $root "local-her") -File -Filter "run*.err" -ErrorAction SilentlyContinue |
    ForEach-Object { Move-Item $_.FullName $logDir -Force; $moved++ }
$report += "  归档日志 {0} 个 → local-her\_logs" -f $moved

# ⑤ 截图归拢（只留最近 6 张）
$shotDir = Join-Path $root "local-her\_shots"
New-Item -ItemType Directory -Force $shotDir | Out-Null
$shots = Get-ChildItem (Join-Path $root "local-her") -File -Filter "*.png" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
$keep = $shots | Select-Object -First 6
$arch = $shots | Select-Object -Skip 6
foreach ($s in $arch) { Move-Item $s.FullName $shotDir -Force }
$report += "  归档截图 {0} 张（保留最新 {1} 张）" -f $arch.Count, $keep.Count

# ⑥ 155 部署中间物
foreach ($f in @("local-her\her155.tgz", "local-her\her-bundle.tgz")) {
    $p = Join-Path $root $f
    if (Test-Path $p) { Remove-Item $p -Force; $report += "  删除中间包 {0}" -f (Split-Path $f -Leaf) }
}
$d = Join-Path $root "local-her\deploy155"
if (Test-Path $d) { Remove-Item $d -Recurse -Force; $report += "  删除 deploy155（部署中间目录）" }

# ⑦ C:\QR 只留最新二维码
$qr = "C:\QR"
if (Test-Path $qr) {
    Get-ChildItem $qr -File | Where-Object { $_.Name -notin @("qr186.png", "qr186-big.png") } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $report += "  清理 C:\QR（只留 qr186.png / qr186-big.png）"
}

$after = SizeOf $root
$report += ""
$report += "  整理前: {0:N1} GB" -f ($before / 1GB)
$report += "  整理后: {0:N1} GB" -f ($after / 1GB)
$report += "  释放:   {0:N1} GB" -f (($before - $after) / 1GB)
$report | ForEach-Object { Write-Output $_ }
