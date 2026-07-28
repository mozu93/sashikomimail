$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "テストに失敗したため、ビルドを中止しました。"
}

python -m PyInstaller --noconfirm --clean sashikomi_mail.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstallerビルドに失敗しました。"
}

Write-Host "ビルド完了: dist\SashikomiMail\SashikomiMail.exe"

