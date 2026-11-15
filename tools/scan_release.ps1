param(
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Report
)
$ErrorActionPreference = 'Stop'
$scanTarget = (Resolve-Path -LiteralPath $Target).Path
$status = Get-MpComputerStatus
if (-not $status.AntivirusEnabled -or -not $status.AMServiceEnabled) {
    throw 'Microsoft Defender is unavailable. No successful scan can be reported.'
}
$scanner = Get-ChildItem -LiteralPath "$env:ProgramData\Microsoft\Windows Defender\Platform" -Directory |
    Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $scanner) { $scanner = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe' }
$output = & $scanner -Scan -ScanType 3 -File $scanTarget -DisableRemediation 2>&1 | Out-String
$scanExit = $LASTEXITCODE
# Return code 0 alone is insufficient: require the scanner's no-threats text.
$clean = $scanExit -eq 0 -and $output -match 'found no threats'
$summary = [ordered]@{
    target = Split-Path -Leaf $scanTarget
    scanned_utc = (Get-Date).ToUniversalTime().ToString('o')
    scanner = 'Microsoft Defender Antivirus'
    engine_version = $status.AMEngineVersion
    signature_version = $status.AntivirusSignatureVersion
    signature_updated_utc = $status.AntivirusSignatureLastUpdated.ToUniversalTime().ToString('o')
    exit_code = $scanExit
    result = $(if ($clean) { 'completed_no_threats_detected' } else { 'not_a_clean_completed_scan' })
    output = $output.Replace($scanTarget, '<release target>').Trim()
}
if (Test-Path -LiteralPath $scanTarget -PathType Leaf) {
    $summary['sha256'] = (Get-FileHash -LiteralPath $scanTarget -Algorithm SHA256).Hash.ToLowerInvariant()
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Report -Encoding utf8
Write-Output "$($summary.target): $($summary.result) (exit $scanExit)"
if (-not $clean) { exit 1 }
