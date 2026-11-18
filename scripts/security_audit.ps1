[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [int]$ExitCode
    )

    if ($ExitCode -ne 0) {
        throw "$Name failed with exit code $ExitCode."
    }
}

function ConvertFrom-AuditJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Output
    )

    $payload = ($Output | ForEach-Object { [string]$_ }) -join "`n"
    if ([string]::IsNullOrWhiteSpace($payload)) {
        throw "$Name returned no parseable audit result."
    }

    try {
        return $payload | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "$Name returned an unparseable audit result."
    }
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$auditRequirements = Join-Path (
    [IO.Path]::GetTempPath()
) ("paper-reader-audit-{0}.txt" -f [Guid]::NewGuid().ToString('N'))
Push-Location -LiteralPath $repositoryRoot
try {
    uv lock --check
    Assert-NativeSuccess -Name 'uv lock check' -ExitCode $LASTEXITCODE

    uv sync --locked --extra dev --extra models --extra ocr --extra packaging
    Assert-NativeSuccess -Name 'locked dependency sync' -ExitCode $LASTEXITCODE

    uv pip check
    Assert-NativeSuccess -Name 'Python dependency consistency check' -ExitCode $LASTEXITCODE

    uv export --locked --all-extras --no-emit-project --format requirements.txt --output-file $auditRequirements --quiet
    Assert-NativeSuccess -Name 'locked requirement export' -ExitCode $LASTEXITCODE

    $pipAuditOutput = @(
        uv run pip-audit --strict --requirement $auditRequirements --require-hashes --disable-pip --format json --progress-spinner off
    )
    $pipAuditExitCode = $LASTEXITCODE
    $pipAudit = ConvertFrom-AuditJson -Name 'pip-audit' -Output $pipAuditOutput
    if (-not ($pipAudit.PSObject.Properties.Name -contains 'dependencies')) {
        throw 'pip-audit returned an unsupported result schema.'
    }
    $pythonVulnerabilities = 0
    foreach ($dependency in @($pipAudit.dependencies)) {
        if (-not ($dependency.PSObject.Properties.Name -contains 'vulns')) {
            throw 'pip-audit returned an incomplete dependency result.'
        }
        $pythonVulnerabilities += @($dependency.vulns).Count
    }
    if ($pipAuditExitCode -ne 0 -or $pythonVulnerabilities -ne 0) {
        throw "pip-audit found $pythonVulnerabilities known vulnerabilities."
    }
    Write-Output "pip-audit: 0 known vulnerabilities across $(@($pipAudit.dependencies).Count) locked third-party packages."

    npm run audit
    Assert-NativeSuccess -Name 'npm dependency audit' -ExitCode $LASTEXITCODE

    $banditOutput = @(uv run bandit -r src scripts -ll -f json --quiet)
    $banditExitCode = $LASTEXITCODE
    $bandit = ConvertFrom-AuditJson -Name 'Bandit' -Output $banditOutput
    if (-not ($bandit.PSObject.Properties.Name -contains 'results')) {
        throw 'Bandit returned an unsupported result schema.'
    }
    $banditFindings = @($bandit.results).Count
    if ($banditExitCode -ne 0 -or $banditFindings -ne 0) {
        throw "Bandit found $banditFindings medium/high findings."
    }
    Write-Output 'Bandit: 0 medium/high findings.'
}
finally {
    if (Test-Path -LiteralPath $auditRequirements -PathType Leaf) {
        Remove-Item -LiteralPath $auditRequirements -Force
    }
    Pop-Location
}
