[CmdletBinding(SupportsShouldProcess = $true)]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rootPrefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$trackedPaths = @(& git -C $root ls-files)
if ($LASTEXITCODE -ne 0) {
  throw 'Cannot inspect tracked files; artifact cleanup was not started.'
}
$relativePaths = @(
  '.coverage',
  '.coverage-core.json',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  '.tmp',
  '.build',
  'build',
  'dist',
  'coverage',
  'htmlcov',
  'playwright-report',
  'test-results',
  '.test-tmp',
  '.rc/.build',
  '.rc/.test-tmp',
  '.rc/.tmp',
  'site/.vitepress/cache',
  'site/.vitepress/dist',
  'site/.vitepress/.temp'
)

function Remove-GeneratedTree {
  param([string]$Target)

  $checkedPath = [System.IO.Path]::GetFullPath($Target)
  if (-not $checkedPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Artifact path escapes the repository.'
  }
  try {
    $item = Get-Item -LiteralPath $checkedPath -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
      Write-Warning 'Retained a link or junction in generated artifacts'
      return
    }
    if ($item.PSIsContainer) {
      foreach ($child in @(Get-ChildItem -LiteralPath $checkedPath -Force)) {
        Remove-GeneratedTree $child.FullName
      }
      if (@(Get-ChildItem -LiteralPath $checkedPath -Force).Count) { return }
    }
    Remove-Item -LiteralPath $checkedPath -Force -ErrorAction Stop
  } catch {
    Write-Warning 'Retained a locked or unavailable generated artifact'
  }
}

function Remove-GeneratedArtifact {
  param([string]$RelativePath)

  $target = [System.IO.Path]::GetFullPath((Join-Path $root $RelativePath))
  if (-not $target.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Artifact path escapes the repository: $RelativePath"
  }
  if (-not (Test-Path -LiteralPath $target)) { return }

  $gitPath = $RelativePath.Replace('\', '/').TrimEnd('/')
  if (@($trackedPaths | Where-Object {
    $_ -eq $gitPath -or $_.StartsWith("$gitPath/", [System.StringComparison]::OrdinalIgnoreCase)
  }).Count) {
    Write-Warning "Skipped $RelativePath because it contains tracked files"
    return
  }
  try {
    $ancestor = $target
    while ($ancestor -ne $root) {
      if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        Write-Warning "Skipped $RelativePath because it crosses a link or junction"
        return
      }
      $ancestor = Split-Path -Parent $ancestor
    }
    if ($PSCmdlet.ShouldProcess($target, 'Remove generated artifact')) {
      Remove-GeneratedTree $target
      if (Test-Path -LiteralPath $target) {
        Write-Output "Cleaned $RelativePath; retained links or locked targets"
      } else {
        Write-Output "Removed $RelativePath"
      }
    }
  } catch {
    Write-Warning "Skipped $RelativePath because it is locked or unavailable"
  }
}

foreach ($relativePath in $relativePaths) {
  Remove-GeneratedArtifact $relativePath
}

foreach ($sourceRoot in @('src', 'tests', 'scripts', 'examples')) {
  $sourcePath = Join-Path $root $sourceRoot
  if (-not (Test-Path -LiteralPath $sourcePath)) { continue }
  $bytecodeDirectories = @(Get-ChildItem -LiteralPath $sourcePath -Recurse -Directory -Filter '__pycache__')
  foreach ($directory in $bytecodeDirectories) {
    Remove-GeneratedArtifact $directory.FullName.Substring($rootPrefix.Length)
  }
}

Get-ChildItem -LiteralPath $root -File -Force |
  Where-Object { $_.Name -like '.coverage*' -or $_.Name -like '*.tsbuildinfo' } |
  ForEach-Object { Remove-GeneratedArtifact $_.Name }
