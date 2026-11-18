[CmdletBinding()]
param(
  [ValidateSet('fast', 'full', 'release')]
  [string]$Scope = 'full',

  [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'

function Invoke-QualityCommand {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [Parameter(Mandatory = $true)]
    [string[]]$Arguments,

    [Parameter(Mandatory = $true)]
    [string]$Display
  )

  Write-Output $Display
  if ($PlanOnly) {
    return
  }

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Quality command failed with exit code ${LASTEXITCODE}: $Display"
  }
}

Invoke-QualityCommand 'uv' @('lock', '--check') 'uv lock --check'
Invoke-QualityCommand 'uv' @('pip', 'check') 'uv pip check'
Invoke-QualityCommand 'uv' @(
  'run',
  'ruff',
  'check',
  'src',
  'tests',
  'scripts',
  'examples'
) 'uv run ruff check src tests scripts examples'
Invoke-QualityCommand 'uv' @(
  'run',
  'python',
  '-m',
  'mypy',
  'src',
  'scripts'
) 'uv run python -m mypy src scripts'
Invoke-QualityCommand 'npm' @('run', 'audit') 'npm run audit'
Invoke-QualityCommand 'npm' @('test') 'npm test'
Invoke-QualityCommand 'npm' @('run', 'typecheck') 'npm run typecheck'
Invoke-QualityCommand 'npm' @('run', 'build') 'npm run build'

if ($Scope -ne 'fast') {
  if (-not $PlanOnly) {
    [System.IO.Directory]::CreateDirectory('.tmp') | Out-Null
  }

  Invoke-QualityCommand 'uv' @(
    'run',
    'pytest',
    '-n',
    '2',
    '--dist=loadscope',
    '-m',
    'not resource_serial',
    '--cov=paper_reader',
    '--cov-branch',
    '--cov-report=',
    '--durations=25',
    '--basetemp=.tmp/p'
  ) 'uv run pytest -n 2 --dist=loadscope -m "not resource_serial" --cov=paper_reader --cov-branch --cov-report= --durations=25 --basetemp=.tmp/p'
  Invoke-QualityCommand 'uv' @(
    'run',
    'pytest',
    '-m',
    'resource_serial',
    '--cov=paper_reader',
    '--cov-branch',
    '--cov-append',
    '--cov-report=term-missing',
    '--cov-report=json:.coverage-core.json',
    '--durations=25',
    '--basetemp=.tmp/s'
  ) 'uv run pytest -m resource_serial --cov=paper_reader --cov-branch --cov-append --cov-report=term-missing --cov-report=json:.coverage-core.json --durations=25 --basetemp=.tmp/s'
  Invoke-QualityCommand 'uv' @(
    'run',
    'python',
    'scripts/check_core_coverage.py',
    '.coverage-core.json',
    '--minimum',
    '90'
  ) 'uv run python scripts/check_core_coverage.py .coverage-core.json --minimum 90'
  Invoke-QualityCommand 'npm' @('run', 'test:e2e') 'npm run test:e2e'
}

if ($Scope -eq 'release') {
  Invoke-QualityCommand 'uv' @(
    'run',
    '--frozen',
    '--extra',
    'packaging',
    'python',
    'scripts/build_windows.py',
    '--output',
    'dist/windows/paper-reader',
    '--work-root',
    '.tmp/windows-build'
  ) 'uv run --frozen --extra packaging python scripts/build_windows.py --output dist/windows/paper-reader --work-root .tmp/windows-build'
  Invoke-QualityCommand 'uv' @(
    'run',
    '--frozen',
    '--extra',
    'packaging',
    'python',
    'scripts/verify_distribution.py',
    'dist/windows/paper-reader'
  ) 'uv run --frozen --extra packaging python scripts/verify_distribution.py dist/windows/paper-reader'
}
