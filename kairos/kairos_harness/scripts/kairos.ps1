[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Command,

    [string]$Workspace = "",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$HarnessRoot = Split-Path -Parent $PSScriptRoot
if (-not $Workspace) {
    $Workspace = Join-Path (Split-Path -Parent $HarnessRoot) "kairos_workspace"
}
Push-Location $HarnessRoot
try {
    & python -m kairos $Command --workspace $Workspace @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
