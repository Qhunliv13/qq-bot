#!/usr/bin/env pwsh
# 推送到 Qhunliv13/qq-bot（2026-10-25）
# 策略：对方只有 main 分支；先 fetch，能 fast-forward 就推 main，否则推一个独立分支
$ErrorActionPreference = "Continue"
Set-Location "C:\qhunliv13_web"

Write-Output "== 1) fetch =="
git fetch qqbot 2>&1 | Select-Object -Last 3

Write-Output ""
Write-Output "== 2) 比较历史（本地 master vs qqbot/main）=="
$base = git merge-base master qqbot/main 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Output "  没有共同祖先（两套独立历史）⇒ 走独立分支"
    $ff = $false
} else {
    $ahead = git rev-list --count "qqbot/main..master" 2>&1
    $behind = git rev-list --count "master..qqbot/main" 2>&1
    Write-Output ("  共同祖先: " + $base)
    Write-Output ("  本地领先: $ahead ；落后: $behind")
    $ff = ($behind.Trim() -eq "0")
}

if ($ff) {
    Write-Output ""
    Write-Output "== 3) 可 ff ⇒ 推 main =="
    git push qqbot master:main 2>&1 | Select-Object -Last 6
} else {
    Write-Output ""
    Write-Output "== 3) 不可 ff ⇒ 推独立分支 her-ops-20261025 =="
    git push qqbot master:refs/heads/her-ops-20261025 2>&1 | Select-Object -Last 6
}

Write-Output ""
Write-Output "== 4) 远端分支现状 =="
git ls-remote --heads qqbot 2>&1 | Select-Object -First 6
