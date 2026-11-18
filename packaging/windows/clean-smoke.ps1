[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$ReleaseDirectory,

  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{64}$")]
  [string]$ExpectedManifestSha256,

  [Parameter(Mandatory = $false)]
  [ValidateRange(8000, 40000)]
  [int]$StartupBudgetMs = 8000,

  [Parameter(Mandatory = $false)]
  [AllowEmptyString()]
  [string]$PreviousReleaseDirectory = "",

  [Parameter(Mandatory = $false)]
  [AllowEmptyString()]
  [string]$ExpectedPreviousManifestSha256 = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Fail-CleanSmoke {
  param([Parameter(Mandatory = $true)][string]$Code)
  throw "paper_reader_clean_smoke:$Code"
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Test-ReparsePoint {
  param([Parameter(Mandatory = $true)][System.IO.FileSystemInfo]$Item)
  return (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Assert-SafeDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if (-not $item.PSIsContainer -or (Test-ReparsePoint -Item $item)) {
    Fail-CleanSmoke "release_directory"
  }
  $full = [System.IO.Path]::GetFullPath($item.FullName).TrimEnd("\")
  $root = [System.IO.Path]::GetPathRoot($full).TrimEnd("\")
  if ($full -eq $root) {
    Fail-CleanSmoke "release_directory"
  }
  return $full
}

function Assert-NoReparseTree {
  param([Parameter(Mandatory = $true)][string]$Root)
  foreach ($item in Get-ChildItem -LiteralPath $Root -Force -Recurse) {
    if (Test-ReparsePoint -Item $item) {
      Fail-CleanSmoke "reparse_member"
    }
  }
}

function Assert-EqualNames {
  param(
    [Parameter(Mandatory = $true)][string[]]$Actual,
    [Parameter(Mandatory = $true)][string[]]$Expected,
    [Parameter(Mandatory = $true)][string]$Code
  )
  $left = @($Actual | Sort-Object)
  $right = @($Expected | Sort-Object)
  if ($left.Count -ne $right.Count -or (($left -join "`n") -cne ($right -join "`n"))) {
    Fail-CleanSmoke $Code
  }
}

function Test-CycloneDxSbom {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][ValidateSet("python", "npm")][string]$Ecosystem
  )
  $bytes = [System.IO.File]::ReadAllBytes($Path)
  if ($bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10 -or $bytes -contains 13) {
    Fail-CleanSmoke "sbom_encoding"
  }
  $bom = [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
  if ($bom.bomFormat -ne "CycloneDX" -or $bom.specVersion -ne "1.6" -or $bom.version -ne 1) {
    Fail-CleanSmoke "sbom_contract"
  }
  $purlType = if ($Ecosystem -ceq "python") { "pypi" } else { "npm" }
  $expectedRoot = "pkg:$purlType/paper-reader@$Version"
  $root = $bom.metadata.component
  if (
    $root.name -ne "paper-reader" -or
    $root.version -ne $Version -or
    $root.type -ne "application" -or
    $root.purl -ne $expectedRoot -or
    $root.'bom-ref' -ne $expectedRoot -or
    @($root.licenses).Count -eq 0
  ) {
    Fail-CleanSmoke "sbom_root"
  }
  $refs = [System.Collections.Generic.HashSet[string]]::new()
  if (-not $refs.Add($expectedRoot)) {
    Fail-CleanSmoke "sbom_graph"
  }
  $components = @($bom.components)
  if ($components.Count -eq 0) {
    Fail-CleanSmoke "sbom_components"
  }
  foreach ($component in $components) {
    $ref = [string]$component.'bom-ref'
    if ([string]::IsNullOrWhiteSpace($ref) -or -not $refs.Add($ref) -or @($component.licenses).Count -eq 0) {
      Fail-CleanSmoke "sbom_components"
    }
  }
  $dependencyRefs = [System.Collections.Generic.HashSet[string]]::new()
  $dependencies = @($bom.dependencies)
  if ($dependencies.Count -ne $refs.Count) {
    Fail-CleanSmoke "sbom_graph"
  }
  foreach ($dependency in $dependencies) {
    $ref = [string]$dependency.ref
    if (-not $refs.Contains($ref) -or -not $dependencyRefs.Add($ref)) {
      Fail-CleanSmoke "sbom_graph"
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($target in @($dependency.dependsOn)) {
      $targetRef = [string]$target
      if (-not $refs.Contains($targetRef) -or -not $seen.Add($targetRef)) {
        Fail-CleanSmoke "sbom_graph"
      }
    }
  }
  return [pscustomobject]@{
    Components = $components.Count
    Dependencies = $dependencies.Count
  }
}

function Test-ReleaseDirectory {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$ExpectedManifestHash,
    [switch]$RequireCurrentContract
  )
  Assert-NoReparseTree -Root $Root
  $children = @(Get-ChildItem -LiteralPath $Root -Force)
  if ($children.Count -ne 10 -or @($children | Where-Object { $_.PSIsContainer }).Count -ne 0) {
    Fail-CleanSmoke "release_members"
  }
  $folded = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
  foreach ($child in $children) {
    if (
      $child.Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$' -or
      -not $folded.Add($child.Name) -or
      (Test-ReparsePoint -Item $child)
    ) {
      Fail-CleanSmoke "release_members"
    }
  }
  $manifestPath = Join-Path $Root "release-manifest.json"
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Fail-CleanSmoke "release_members"
  }
  if ((Get-Sha256 -Path $manifestPath) -cne $ExpectedManifestHash) {
    Fail-CleanSmoke "release_manifest_hash"
  }
  $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
  if ($manifestBytes.Length -eq 0 -or $manifestBytes[$manifestBytes.Length - 1] -ne 10 -or $manifestBytes -contains 13) {
    Fail-CleanSmoke "release_manifest_encoding"
  }
  $manifest = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
  if (
    $manifest.schema_version -ne "paper-reader-release-manifest-v1" -or
    $manifest.api_version -ne "v1" -or
    $manifest.openapi_version -ne $manifest.version -or
    $manifest.openapi_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
    $manifest.bundle_schema_version -ne "paper-reader-bundle-manifest-v1" -or
    $manifest.plugin_protocol_version -ne "1.0" -or
    $manifest.database_schema_revision -cnotmatch '^[0-9]{4}_[a-z0-9_]+$' -or
    $manifest.platform.operating_system -ne "windows" -or
    $manifest.platform.architecture -ne "x86_64" -or
    $manifest.platform.minimum_windows_build -ne 22000 -or
    $manifest.signed -ne $false -or
    $manifest.version -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
    $manifest.git_commit -cnotmatch '^[0-9a-f]{40}([0-9a-f]{24})?$'
  ) {
    Fail-CleanSmoke "release_manifest_contract"
  }
  if (
    $RequireCurrentContract -and (
      $manifest.openapi_sha256 -ne "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a" -or
      $manifest.database_schema_revision -ne "0038_remove_legacy_conversations"
    )
  ) {
    Fail-CleanSmoke "release_manifest_current_contract"
  }
  $version = [string]$manifest.version
  $expectedNames = @(
    "CAPABILITY_MATRIX.md",
    "CHANGELOG.md",
    "paper-reader-$version-npm.cdx.json",
    "paper-reader-$version-python.cdx.json",
    "paper-reader-$version-windows-x64-portable.zip",
    "paper-reader-$version-windows-x64-setup.exe",
    "RELEASE_NOTES.md",
    "release-manifest.json",
    "SHA256SUMS.txt",
    "UNSIGNED.md"
  )
  Assert-EqualNames -Actual @($children.Name) -Expected $expectedNames -Code "release_members"
  Assert-EqualNames -Actual @($manifest.required_assets) -Expected $expectedNames -Code "release_required_assets"

  $expectedRoles = @(
    "capability_matrix", "changelog", "installer", "npm_sbom", "portable",
    "python_sbom", "release_notes", "unsigned_notice"
  )
  $assets = @($manifest.assets)
  if ($assets.Count -ne 8) {
    Fail-CleanSmoke "release_assets"
  }
  Assert-EqualNames -Actual @($assets.role) -Expected $expectedRoles -Code "release_assets"
  foreach ($asset in $assets) {
    $name = [string]$asset.name
    $path = Join-Path $Root $name
    if (
      -not (Test-Path -LiteralPath $path -PathType Leaf) -or
      [int64]$asset.size_bytes -ne (Get-Item -LiteralPath $path).Length -or
      [string]$asset.sha256 -cne (Get-Sha256 -Path $path)
    ) {
      Fail-CleanSmoke "release_asset_drift"
    }
  }

  $sumPath = Join-Path $Root "SHA256SUMS.txt"
  $sumBytes = [System.IO.File]::ReadAllBytes($sumPath)
  if ($sumBytes.Length -eq 0 -or $sumBytes[$sumBytes.Length - 1] -ne 10 -or $sumBytes -contains 13) {
    Fail-CleanSmoke "release_checksums_format"
  }
  $sumLines = [System.IO.File]::ReadAllLines($sumPath, [System.Text.Encoding]::ASCII)
  if ($sumLines.Count -ne 9) {
    Fail-CleanSmoke "release_checksums_members"
  }
  $sumNames = New-Object 'System.Collections.Generic.List[string]'
  foreach ($line in $sumLines) {
    if ($line -cnotmatch '^(?<hash>[0-9a-f]{64})  (?<name>[A-Za-z0-9][A-Za-z0-9._-]{0,199})$') {
      Fail-CleanSmoke "release_checksums_format"
    }
    $name = $Matches.name
    $sumNames.Add($name)
    if ((Get-Sha256 -Path (Join-Path $Root $name)) -cne $Matches.hash) {
      Fail-CleanSmoke "release_checksum_drift"
    }
  }
  Assert-EqualNames -Actual @($sumNames) -Expected @($expectedNames | Where-Object { $_ -ne "SHA256SUMS.txt" }) -Code "release_checksums_members"

  $python = Test-CycloneDxSbom -Path (Join-Path $Root "paper-reader-$version-python.cdx.json") -Version $version -Ecosystem "python"
  $npm = Test-CycloneDxSbom -Path (Join-Path $Root "paper-reader-$version-npm.cdx.json") -Version $version -Ecosystem "npm"
  $unsigned = [System.IO.File]::ReadAllText((Join-Path $Root "UNSIGNED.md"), [System.Text.Encoding]::UTF8)
  if ($unsigned -notmatch 'not digitally signed' -or $unsigned -notmatch 'SmartScreen') {
    Fail-CleanSmoke "unsigned_notice"
  }
  return [pscustomobject]@{
    Version = $version
    Installer = Join-Path $Root "paper-reader-$version-windows-x64-setup.exe"
    Portable = Join-Path $Root "paper-reader-$version-windows-x64-portable.zip"
    PythonComponents = $python.Components
    PythonDependencies = $python.Dependencies
    NpmComponents = $npm.Components
    NpmDependencies = $npm.Dependencies
  }
}

function Expand-SafePortableArchive {
  param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipArchive]$archive = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
  try {
    $entries = @($archive.Entries)
    if ($entries.Count -eq 0 -or $entries.Count -gt 100000) {
      Fail-CleanSmoke "portable_members"
    }
    $names = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    [int64]$total = 0
    foreach ($entry in $entries) {
      $name = [string]$entry.FullName
      if (
        [string]::IsNullOrWhiteSpace($name) -or
        $name.Contains("\") -or
        $name.Contains(":") -or
        $name.StartsWith("/") -or
        $name.Normalize([System.Text.NormalizationForm]::FormC) -cne $name -or
        -not $names.Add($name)
      ) {
        Fail-CleanSmoke "portable_member_path"
      }
      $parts = @($name.Split('/'))
      if ($parts[0] -cne "paper-reader" -or @($parts | Where-Object { $_ -eq ".." -or $_ -eq "." }).Count -ne 0) {
        Fail-CleanSmoke "portable_member_path"
      }
      $unixMode = ($entry.ExternalAttributes -shr 16) -band 0xF000
      if ($unixMode -eq 0xA000) {
        Fail-CleanSmoke "portable_link"
      }
      $total += [int64]$entry.Length
      if ($total -gt 21474836480) {
        Fail-CleanSmoke "portable_size"
      }
    }
  } finally {
    $archive.Dispose()
  }
  New-Item -ItemType Directory -Path $Destination -ErrorAction Stop | Out-Null
  [System.IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $Destination)
  $root = Join-Path $Destination "paper-reader"
  if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    Fail-CleanSmoke "portable_root"
  }
  Assert-NoReparseTree -Root $root
  return $root
}

function Assert-DistributionManifest {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Version
  )
  $manifestPath = Join-Path $Root "release-manifest.json"
  $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
  if (
    $manifest.schema_version -ne "paper-reader-bundle-manifest-v1" -or
    $manifest.application -ne "paper-reader" -or
    $manifest.version -ne $Version -or
    $manifest.platform -ne "windows" -or
    $manifest.architecture -ne "x86_64" -or
    $manifest.bundle_mode -ne "onedir" -or
    $manifest.entrypoint -ne "paper-reader.exe"
  ) {
    Fail-CleanSmoke "distribution_manifest"
  }
  $records = @($manifest.files)
  $recordNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
  foreach ($record in $records) {
    $relative = [string]$record.path
    if (
      [string]::IsNullOrWhiteSpace($relative) -or
      $relative.Contains("\") -or
      $relative.Contains(":") -or
      $relative.StartsWith("/") -or
      -not $recordNames.Add($relative)
    ) {
      Fail-CleanSmoke "distribution_manifest"
    }
    $path = Join-Path $Root ($relative.Replace('/', '\'))
    if (
      -not (Test-Path -LiteralPath $path -PathType Leaf) -or
      [int64]$record.size_bytes -ne (Get-Item -LiteralPath $path).Length -or
      [string]$record.sha256 -cne (Get-Sha256 -Path $path)
    ) {
      Fail-CleanSmoke "distribution_drift"
    }
  }
  $actual = @(
    Get-ChildItem -LiteralPath $Root -File -Recurse | ForEach-Object {
      $_.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
    } | Where-Object { $_ -ne "release-manifest.json" }
  )
  Assert-EqualNames -Actual $actual -Expected @($recordNames) -Code "distribution_members"
  return $records.Count
}

function Get-TreeSignature {
  param([Parameter(Mandatory = $true)][string]$Root)
  $lines = New-Object 'System.Collections.Generic.List[string]'
  foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName) {
    $relative = $file.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
    $lines.Add("$relative|$($file.Length)|$(Get-Sha256 -Path $file.FullName)")
  }
  $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
  $hasher = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([System.BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $hasher.Dispose()
  }
}

function Set-ReadOnlyTree {
  param([Parameter(Mandatory = $true)][string]$Root)
  foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse) {
    $file.Attributes = $file.Attributes -bor [System.IO.FileAttributes]::ReadOnly
  }
}

function Clear-ReadOnlyTree {
  param([Parameter(Mandatory = $true)][string]$Root)
  if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    return
  }
  foreach ($item in Get-ChildItem -LiteralPath $Root -Force -Recurse) {
    if (-not $item.PSIsContainer) {
      $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    }
  }
}

function ConvertTo-NativeArgument {
  param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
  if ($Value.Length -eq 0) {
    return '""'
  }
  if ($Value -notmatch '[\s"]') {
    return $Value
  }
  return '"' + $Value.Replace('"', '\"') + '"'
}

function Start-NativeProcess {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
  )
  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $FilePath
  $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument -Value $_ }) -join ' ')
  $info.WorkingDirectory = $WorkingDirectory
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $info
  if (-not $process.Start()) {
    Fail-CleanSmoke "process_start"
  }
  return $process
}

function Wait-NativeProcess {
  param(
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
    [Parameter(Mandatory = $true)][string]$Code
  )
  if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
    try { $Process.Kill() } catch { }
    Fail-CleanSmoke "$Code`_timeout"
  }
  if ($Process.ExitCode -ne 0) {
    Fail-CleanSmoke $Code
  }
}

function Get-LoopbackText {
  param([Parameter(Mandatory = $true)][string]$Uri)
  $loopbackPattern = '^http' + '://127\.0\.0\.1:[0-9]+/'
  if ($Uri -cnotmatch $loopbackPattern) {
    Fail-CleanSmoke "loopback_uri"
  }
  $request = [System.Net.HttpWebRequest]::Create($Uri)
  $request.Proxy = $null
  $request.AllowAutoRedirect = $false
  $request.Timeout = 5000
  $request.ReadWriteTimeout = 5000
  $response = $request.GetResponse()
  try {
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
  } finally {
    $response.Dispose()
  }
}

function Wait-AppReady {
  param(
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)][string]$ReadyPath,
    [Parameter(Mandatory = $true)][string]$Version
  )
  $deadline = [System.DateTime]::UtcNow.AddSeconds(35)
  while (-not (Test-Path -LiteralPath $ReadyPath -PathType Leaf)) {
    if ($Process.HasExited) {
      Fail-CleanSmoke "app_startup"
    }
    if ([System.DateTime]::UtcNow -ge $deadline) {
      Fail-CleanSmoke "app_ready_timeout"
    }
    Start-Sleep -Milliseconds 100
  }
  $ready = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReadyPath | ConvertFrom-Json
  $port = [int]$ready.port
  if ($ready.schema_version -ne "paper-reader-ready-v1" -or $port -le 0 -or $port -gt 65535) {
    Fail-CleanSmoke "app_ready"
  }
  $healthText = Get-LoopbackText -Uri "http://127.0.0.1:$port/api/v1/health"
  $health = $healthText | ConvertFrom-Json
  if (
    $health.name -ne "paper-reader" -or
    $health.status -ne "ok" -or
    $health.version -ne $Version -or
    $health.api_version -ne "v1" -or
      $health.openapi_sha256 -ne "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a" -or
    $health.database_schema_revision -ne "0038_remove_legacy_conversations" -or
    $health.plugin_protocol_version -ne "1.0"
  ) {
    Fail-CleanSmoke "app_health"
  }
  $spa = Get-LoopbackText -Uri "http://127.0.0.1:$port/"
  if ($spa -notmatch '<div id="app"></div>') {
    Fail-CleanSmoke "app_spa"
  }
  return $port
}

function Get-BrowserProcessIds {
  $ids = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
    if ($process.ProcessName.ToLowerInvariant() -in @("chrome", "msedge", "firefox", "brave")) {
      [void]$ids.Add($process.Id)
    }
  }
  return ,$ids
}

function Assert-NoForbiddenDescendants {
  param([Parameter(Mandatory = $true)][int]$RootProcessId)
  $processes = @(Get-CimInstance Win32_Process)
  $descendants = [System.Collections.Generic.HashSet[int]]::new()
  [void]$descendants.Add($RootProcessId)
  $changed = $true
  while ($changed) {
    $changed = $false
    foreach ($process in $processes) {
      if ($descendants.Contains([int]$process.ParentProcessId) -and $descendants.Add([int]$process.ProcessId)) {
        $changed = $true
      }
    }
  }
  foreach ($process in $processes) {
    if (-not $descendants.Contains([int]$process.ProcessId) -or [int]$process.ProcessId -eq $RootProcessId) {
      continue
    }
    $name = ([string]$process.Name).ToLowerInvariant()
    if ($name -in @("python.exe", "node.exe", "git.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe")) {
      Fail-CleanSmoke "forbidden_child_process"
    }
  }
}

function Set-AppRoots {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Name
  )
  $base = Join-Path $Root $Name
  $state = Join-Path $base "state"
  $cache = Join-Path $base "cache"
  $library = Join-Path $base "library"
  foreach ($path in @($state, $cache, $library)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
  }
  $env:PAPER_READER_STATE_ROOT = $state
  $env:PAPER_READER_CACHE_ROOT = $cache
  $env:PAPER_READER_LIBRARY_ROOT = $library
  return [pscustomobject]@{ State = $state; Cache = $cache; Library = $library }
}

function Invoke-AppActivationSmoke {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Scratch,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][int]$StartupBudgetMs,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$TrackedProcesses
  )
  $roots = Set-AppRoots -Root $Scratch -Name "activation"
  $ready = Join-Path $roots.State "ready.json"
  $browsersBefore = Get-BrowserProcessIds
  $startup = [System.Diagnostics.Stopwatch]::StartNew()
  $primary = Start-NativeProcess -FilePath $Executable -Arguments @(
    "serve", "--no-browser", "--ready-file", $ready, "--shutdown-after", "9"
  ) -WorkingDirectory $roots.Cache
  [void]$TrackedProcesses.Add($primary)
  try {
    [void](Wait-AppReady -Process $primary -ReadyPath $ready -Version $Version)
  } finally {
    $startup.Stop()
  }
  $startupMs = [System.Math]::Round($startup.Elapsed.TotalMilliseconds, 3)
  $script:observedStartupMs = $startupMs
  if ($startupMs -gt $StartupBudgetMs) {
    Fail-CleanSmoke "startup_budget"
  }
  Assert-NoForbiddenDescendants -RootProcessId $primary.Id
  $secondary = Start-NativeProcess -FilePath $Executable -Arguments @("serve", "--no-browser") -WorkingDirectory $roots.Cache
  [void]$TrackedProcesses.Add($secondary)
  Wait-NativeProcess -Process $secondary -TimeoutSeconds 15 -Code "second_instance"
  Assert-NoForbiddenDescendants -RootProcessId $primary.Id
  Wait-NativeProcess -Process $primary -TimeoutSeconds 20 -Code "primary_shutdown"
  $browsersAfter = Get-BrowserProcessIds
  foreach ($id in $browsersAfter) {
    if (-not $browsersBefore.Contains($id)) {
      Fail-CleanSmoke "browser_spawned"
    }
  }
  return [pscustomobject]@{
    Roots = $roots
    StartupMs = $startupMs
  }
}

function Invoke-CrashRecoverySmoke {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Scratch,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$TrackedProcesses
  )
  $roots = Set-AppRoots -Root $Scratch -Name "crash-recovery"
  $firstReady = Join-Path $roots.State "first-ready.json"
  $first = Start-NativeProcess -FilePath $Executable -Arguments @(
    "serve", "--no-browser", "--ready-file", $firstReady, "--shutdown-after", "60"
  ) -WorkingDirectory $roots.Cache
  [void]$TrackedProcesses.Add($first)
  [void](Wait-AppReady -Process $first -ReadyPath $firstReady -Version $Version)
  Assert-NoForbiddenDescendants -RootProcessId $first.Id
  $first.Kill()
  [void]$first.WaitForExit(10000)
  Start-Sleep -Milliseconds 500
  $secondReady = Join-Path $roots.State "second-ready.json"
  $second = Start-NativeProcess -FilePath $Executable -Arguments @(
    "serve", "--no-browser", "--ready-file", $secondReady, "--shutdown-after", "5"
  ) -WorkingDirectory $roots.Cache
  [void]$TrackedProcesses.Add($second)
  [void](Wait-AppReady -Process $second -ReadyPath $secondReady -Version $Version)
  Assert-NoForbiddenDescendants -RootProcessId $second.Id
  Wait-NativeProcess -Process $second -TimeoutSeconds 15 -Code "crash_recovery"
}

function Get-FirewallFailureCode {
  param([Parameter(Mandatory = $true)][System.Management.Automation.ErrorRecord]$Record)
  $category = [string]$Record.CategoryInfo.Category
  switch ($category) {
    "PermissionDenied" { return "firewall_permission_denied" }
    "SecurityError" { return "firewall_permission_denied" }
    "InvalidArgument" { return "firewall_invalid_argument" }
    "ObjectNotFound" { return "firewall_service_unavailable" }
    "ResourceUnavailable" { return "firewall_service_unavailable" }
    "ConnectionError" { return "firewall_service_unavailable" }
    default { return "firewall_rule_create_failed" }
  }
}

function Assert-OfflineFirewallCapability {
  $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail-CleanSmoke "firewall_requires_administrator"
  }
  $service = Get-Service -Name "MpsSvc" -ErrorAction SilentlyContinue
  if ($null -eq $service -or $service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
    Fail-CleanSmoke "firewall_service_unavailable"
  }
}

function Add-OfflineFirewallRule {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$RuleNames
  )
  Assert-OfflineFirewallCapability
  $name = "paper-reader-clean-smoke-$([System.Guid]::NewGuid().ToString('N'))"
  try {
    $rule = New-NetFirewallRule -Name $name -DisplayName $name -Direction Outbound -Action Block -Program $Executable -Protocol Any -RemoteAddress Any -Profile Any -ErrorAction Stop
  } catch {
    Fail-CleanSmoke (Get-FirewallFailureCode -Record $_)
  }
  [void]$RuleNames.Add($name)
  try {
    $addressFilter = $rule | Get-NetFirewallAddressFilter -ErrorAction Stop
  } catch {
    Fail-CleanSmoke "firewall_scope_unverified"
  }
  $remoteAddresses = @($addressFilter.RemoteAddress | ForEach-Object { [string]$_ })
  if ($remoteAddresses.Count -ne 1 -or $remoteAddresses[0] -cne "Any") {
    Fail-CleanSmoke "firewall_scope_unverified"
  }
}

function Invoke-InstallerLifecycle {
  param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$BaseInstaller,
    [Parameter(Mandatory = $true)][string]$Scratch,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$BaseVersion,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$TrackedProcesses,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$RuleNames
  )
  $isUpgrade = $BaseVersion -cne $Version
  $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{381B261E-B659-4B56-855E-ADDE80CC5BD2}_is1'
  if (Test-Path -LiteralPath $uninstallKey) {
    Fail-CleanSmoke "installer_already_present"
  }
  # Build "安装 目录" from code points for Windows PowerShell 5's no-BOM parser.
  $installName = ([char]0x5B89) + ([char]0x88C5) + " " + ([char]0x76EE) + ([char]0x5F55)
  $installRoot = Join-Path (Join-Path $Scratch $installName) "paper-reader"
  $groupName = "paper-reader-clean-smoke-$([System.Guid]::NewGuid().ToString('N'))"
  $arguments = @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCLOSEAPPLICATIONS",
    "/NOFORCECLOSEAPPLICATIONS", "/SP-", "/DIR=$installRoot", "/GROUP=$groupName",
    "/MERGETASKS=!desktopicon"
  )
  $install = Start-NativeProcess -FilePath $BaseInstaller -Arguments $arguments -WorkingDirectory $Scratch
  [void]$TrackedProcesses.Add($install)
  Wait-NativeProcess -Process $install -TimeoutSeconds 180 -Code "installer_install"
  $installedExecutable = Join-Path $installRoot "paper-reader.exe"
  if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf) -or -not (Test-Path -LiteralPath $uninstallKey)) {
    Fail-CleanSmoke "installer_installed"
  }
  $registry = Get-ItemProperty -LiteralPath $uninstallKey
  if ($registry.DisplayVersion -ne $BaseVersion -or [System.IO.Path]::GetFullPath($registry.InstallLocation).TrimEnd('\') -cne [System.IO.Path]::GetFullPath($installRoot).TrimEnd('\')) {
    Fail-CleanSmoke "installer_registry"
  }
  Add-OfflineFirewallRule -Executable $installedExecutable -RuleNames $RuleNames
  $installedRoots = Set-AppRoots -Root $Scratch -Name "installed-user-data"
  $installedReady = Join-Path $installedRoots.State "installed-ready.json"
  $application = Start-NativeProcess -FilePath $installedExecutable -Arguments @(
    "serve", "--no-browser", "--ready-file", $installedReady, "--shutdown-after", "5"
  ) -WorkingDirectory $installedRoots.Cache
  [void]$TrackedProcesses.Add($application)
  [void](Wait-AppReady -Process $application -ReadyPath $installedReady -Version $BaseVersion)
  Assert-NoForbiddenDescendants -RootProcessId $application.Id
  Wait-NativeProcess -Process $application -TimeoutSeconds 15 -Code "installed_app"
  $canary = Join-Path $installedRoots.State "preserve.canary"
  [System.IO.File]::WriteAllText($canary, "preserve", [System.Text.Encoding]::UTF8)

  $update = Start-NativeProcess -FilePath $Installer -Arguments $arguments -WorkingDirectory $Scratch
  [void]$TrackedProcesses.Add($update)
  Wait-NativeProcess -Process $update -TimeoutSeconds 180 -Code "installer_update"
  if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf) -or -not (Test-Path -LiteralPath $canary -PathType Leaf)) {
    Fail-CleanSmoke "installer_updated"
  }
  $updatedRegistry = Get-ItemProperty -LiteralPath $uninstallKey
  if ($updatedRegistry.DisplayVersion -ne $Version -or [System.IO.Path]::GetFullPath($updatedRegistry.InstallLocation).TrimEnd('\') -cne [System.IO.Path]::GetFullPath($installRoot).TrimEnd('\')) {
    Fail-CleanSmoke "installer_updated"
  }
  $updatedReady = Join-Path $installedRoots.State "updated-ready.json"
  $updatedApplication = Start-NativeProcess -FilePath $installedExecutable -Arguments @(
    "serve", "--no-browser", "--ready-file", $updatedReady, "--shutdown-after", "5"
  ) -WorkingDirectory $installedRoots.Cache
  [void]$TrackedProcesses.Add($updatedApplication)
  [void](Wait-AppReady -Process $updatedApplication -ReadyPath $updatedReady -Version $Version)
  Assert-NoForbiddenDescendants -RootProcessId $updatedApplication.Id
  Wait-NativeProcess -Process $updatedApplication -TimeoutSeconds 15 -Code "updated_app"

  $uninstaller = Join-Path $installRoot "unins000.exe"
  if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    Fail-CleanSmoke "uninstaller_missing"
  }
  $uninstall = Start-NativeProcess -FilePath $uninstaller -Arguments @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
  ) -WorkingDirectory $Scratch
  [void]$TrackedProcesses.Add($uninstall)
  Wait-NativeProcess -Process $uninstall -TimeoutSeconds 180 -Code "installer_uninstall"
  if ((Test-Path -LiteralPath $uninstallKey) -or (Test-Path -LiteralPath $installedExecutable)) {
    Fail-CleanSmoke "installer_uninstalled"
  }
  if (-not (Test-Path -LiteralPath $canary -PathType Leaf)) {
    Fail-CleanSmoke "user_data_preserved"
  }
  return [pscustomobject]@{
    installer_installed = $true
    installer_reinstalled = (-not $isUpgrade)
    installer_upgraded = $isUpgrade
    installer_uninstalled = $true
    user_data_preserved = $true
  }
}

function Remove-OwnedScratch {
  param(
    [Parameter(Mandatory = $true)][string]$Scratch,
    [Parameter(Mandatory = $true)][string]$TempRoot
  )
  if (-not (Test-Path -LiteralPath $Scratch -PathType Container)) {
    return
  }
  $full = [System.IO.Path]::GetFullPath($Scratch).TrimEnd('\')
  $parent = [System.IO.Path]::GetFullPath($TempRoot).TrimEnd('\')
  $prefix = $parent + '\'
  if (
    -not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    [System.IO.Path]::GetFileName($full) -cnotmatch '^paper-reader-clean-smoke-[0-9a-f]{32}$'
  ) {
    Fail-CleanSmoke "scratch_boundary"
  }
  $lastError = $null
  for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
      return
    }
    try {
      Clear-ReadOnlyTree -Root $full
      Assert-NoReparseTree -Root $full
      Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
      if (-not (Test-Path -LiteralPath $full)) {
        return
      }
    } catch {
      $lastError = $_
    }
    Start-Sleep -Milliseconds 250
  }
  if ($null -ne $lastError) {
    throw $lastError
  }
  Fail-CleanSmoke "scratch_cleanup"
}

function Write-CleanSmokeEvidence {
  param(
    [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Evidence,
    [Parameter(Mandatory = $true)][string]$Path
  )
  $json = $Evidence | ConvertTo-Json -Depth 6 -Compress
  [System.IO.File]::WriteAllText($Path, $json + "`n", [System.Text.UTF8Encoding]::new($false))
  Write-Output $json
}

$originalEnvironment = [System.Environment]::GetEnvironmentVariables("Process")
$evidenceRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$evidencePath = Join-Path ([System.IO.Path]::GetFullPath($evidenceRoot)) "paper-reader-clean-smoke-evidence.json"
$harnessSha256 = if (
  -not [string]::IsNullOrWhiteSpace($PSCommandPath) -and
  (Test-Path -LiteralPath $PSCommandPath -PathType Leaf)
) {
  Get-Sha256 -Path $PSCommandPath
} else {
  ""
}
$tempRoot = [System.IO.Path]::GetFullPath($env:TEMP)
$scratch = Join-Path $tempRoot "paper-reader-clean-smoke-$([System.Guid]::NewGuid().ToString('N'))"
$trackedProcesses = New-Object System.Collections.ArrayList
$firewallRules = New-Object System.Collections.ArrayList
$evidence = $null
$previousManifestForEvidence = $null
$previousReleaseVersion = $null
$cleanupFailed = $false
$cleanupErrorCode = $null
$stage = "initialization"
$observedStartupMs = $null

try {
  if ($harnessSha256 -cnotmatch '^[0-9a-f]{64}$') {
    Fail-CleanSmoke "harness_identity"
  }
  $stage = "upgrade_identity"
  $hasPreviousReleaseDirectory = -not [string]::IsNullOrWhiteSpace($PreviousReleaseDirectory)
  $hasPreviousManifestHash = -not [string]::IsNullOrWhiteSpace($ExpectedPreviousManifestSha256)
  if ($hasPreviousReleaseDirectory -ne $hasPreviousManifestHash) {
    Fail-CleanSmoke "upgrade_identity_pair"
  }
  if ($hasPreviousManifestHash) {
    if ($ExpectedPreviousManifestSha256 -cnotmatch '^[0-9a-f]{64}$') {
      Fail-CleanSmoke "upgrade_manifest_hash"
    }
    $previousManifestForEvidence = $ExpectedPreviousManifestSha256
  }
  $stage = "release_validation"
  $releaseRoot = Assert-SafeDirectory -Path $ReleaseDirectory
  $release = Test-ReleaseDirectory -Root $releaseRoot -ExpectedManifestHash $ExpectedManifestSha256 -RequireCurrentContract
  $previousRelease = $null
  if ($hasPreviousReleaseDirectory) {
    $stage = "previous_release_validation"
    $previousReleaseRoot = Assert-SafeDirectory -Path $PreviousReleaseDirectory
    if ([string]::Equals($previousReleaseRoot, $releaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
      Fail-CleanSmoke "upgrade_release_collision"
    }
    $previousRelease = Test-ReleaseDirectory -Root $previousReleaseRoot -ExpectedManifestHash $ExpectedPreviousManifestSha256
    $previousReleaseVersion = $previousRelease.Version
    if (([version]$previousRelease.Version).CompareTo([version]$release.Version) -ge 0) {
      Fail-CleanSmoke "upgrade_version_order"
    }
  }
  $stage = "scratch_setup"
  New-Item -ItemType Directory -Path $scratch -ErrorAction Stop | Out-Null
  $childTemp = Join-Path $scratch "temp"
  $appData = Join-Path $scratch "appdata"
  $localAppData = Join-Path $scratch "localappdata"
  foreach ($path in @($childTemp, $appData, $localAppData)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
  }
  foreach ($entry in [System.Environment]::GetEnvironmentVariables("Process").GetEnumerator()) {
    $name = [string]$entry.Key
    if ($name -match '(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)') {
      [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
  }
  $env:PATH = Join-Path $env:SystemRoot "System32"
  $env:TEMP = $childTemp
  $env:TMP = $childTemp
  $env:APPDATA = $appData
  $env:LOCALAPPDATA = $localAppData
  $env:HTTP_PROXY = ""
  $env:HTTPS_PROXY = ""
  $env:NO_PROXY = "127.0.0.1,localhost"
  foreach ($tool in @("python.exe", "node.exe", "git.exe")) {
    if (Get-Command $tool -ErrorAction SilentlyContinue) {
      Fail-CleanSmoke "external_tool_on_path"
    }
  }

  $stage = "portable_extract"
  # Contract fixture label: "研究验证 含空格". Build it from code points so
  # Windows PowerShell 5 can parse this UTF-8-without-BOM script reliably.
  $unicodeName = ([char]0x7814) + ([char]0x7A76) + ([char]0x9A8C) + ([char]0x8BC1) + " " + ([char]0x542B) + ([char]0x7A7A) + ([char]0x683C)
  $unicodeRoot = Join-Path $scratch $unicodeName
  New-Item -ItemType Directory -Path $unicodeRoot -ErrorAction Stop | Out-Null
  $distribution = Expand-SafePortableArchive -ArchivePath $release.Portable -Destination (Join-Path $unicodeRoot "portable")
  $stage = "distribution_validation"
  $distributionFileCount = Assert-DistributionManifest -Root $distribution -Version $release.Version
  $beforeSignature = Get-TreeSignature -Root $distribution
  Set-ReadOnlyTree -Root $distribution
  $portableExecutable = Join-Path $distribution "paper-reader.exe"
  $stage = "offline_firewall"
  Add-OfflineFirewallRule -Executable $portableExecutable -RuleNames $firewallRules
  $stage = "portable_activation"
  $activation = Invoke-AppActivationSmoke -Executable $portableExecutable -Scratch $scratch -Version $release.Version -StartupBudgetMs $StartupBudgetMs -TrackedProcesses $trackedProcesses
  $stage = "crash_recovery"
  Invoke-CrashRecoverySmoke -Executable $portableExecutable -Scratch $scratch -Version $release.Version -TrackedProcesses $trackedProcesses
  $stage = "program_integrity"
  $afterSignature = Get-TreeSignature -Root $distribution
  if ($beforeSignature -cne $afterSignature) {
    Fail-CleanSmoke "program_bytes_unchanged"
  }
  $stage = "installer_lifecycle"
  $baseInstaller = if ($null -eq $previousRelease) { $release.Installer } else { $previousRelease.Installer }
  $baseVersion = if ($null -eq $previousRelease) { $release.Version } else { $previousRelease.Version }
  $lifecycle = Invoke-InstallerLifecycle -Installer $release.Installer -BaseInstaller $baseInstaller -Scratch $scratch -Version $release.Version -BaseVersion $baseVersion -TrackedProcesses $trackedProcesses -RuleNames $firewallRules
  $stage = "evidence_assembly"
  $evidence = [ordered]@{
    schema_version = "paper-reader-clean-smoke-evidence-v1"
    ok = $true
    release_manifest_sha256 = $ExpectedManifestSha256
    harness_sha256 = $harnessSha256
    release_version = $release.Version
    previous_release_manifest_sha256 = $previousManifestForEvidence
    previous_release_version = $previousReleaseVersion
    release_file_count = 10
    checksums_verified = 9
    python_sbom_components = $release.PythonComponents
    python_sbom_dependencies = $release.PythonDependencies
    npm_sbom_components = $release.NpmComponents
    npm_sbom_dependencies = $release.NpmDependencies
    distribution_file_count = $distributionFileCount
    dynamic_port = $true
    browser_disabled = $true
    forbidden_child_processes = 0
    second_instance_activated = $true
    crash_lock_recovered = $true
    runtime_contracts_verified = $true
    portable_startup_ms = $activation.StartupMs
    startup_budget_ms = $StartupBudgetMs
    startup_within_budget = $true
    offline_startup = $true
    outbound_firewall_blocked = $true
    program_bytes_unchanged = $true
    installer_installed = $lifecycle.installer_installed
    installer_reinstalled = $lifecycle.installer_reinstalled
    installer_upgraded = $lifecycle.installer_upgraded
    upgrade_verified = $lifecycle.installer_upgraded
    installer_uninstalled = $lifecycle.installer_uninstalled
    user_data_preserved = $lifecycle.user_data_preserved
  }
} catch {
  $message = [string]$_.Exception.Message
  $code = "clean_smoke_failed"
  if ($message.StartsWith("paper_reader_clean_smoke:", [System.StringComparison]::Ordinal)) {
    $candidate = $message.Substring("paper_reader_clean_smoke:".Length)
    if ($candidate -cmatch '^[a-z0-9_]{1,80}$') {
      $code = $candidate
    }
  } elseif ($stage -cmatch '^[a-z0-9_]{1,60}$') {
    $code = "unexpected_$stage"
  }
  $evidence = [ordered]@{
    schema_version = "paper-reader-clean-smoke-evidence-v1"
    ok = $false
    release_manifest_sha256 = $ExpectedManifestSha256
    harness_sha256 = $harnessSha256
    previous_release_manifest_sha256 = $previousManifestForEvidence
    error_code = $code
    failure_stage = $stage
    portable_startup_ms = $observedStartupMs
    startup_budget_ms = $StartupBudgetMs
  }
} finally {
  foreach ($process in $trackedProcesses) {
    try {
      if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill()
        [void]$process.WaitForExit(10000)
      }
    } catch {
      $cleanupFailed = $true
      if ($null -eq $cleanupErrorCode) { $cleanupErrorCode = "cleanup_process" }
    }
  }
  foreach ($rule in $firewallRules) {
    try { Remove-NetFirewallRule -Name $rule -ErrorAction Stop | Out-Null } catch {
      $cleanupFailed = $true
      if ($null -eq $cleanupErrorCode) { $cleanupErrorCode = "cleanup_firewall" }
    }
  }
  try {
    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{381B261E-B659-4B56-855E-ADDE80CC5BD2}_is1'
    if (Test-Path -LiteralPath $uninstallKey) {
      $registry = Get-ItemProperty -LiteralPath $uninstallKey
      $location = [System.IO.Path]::GetFullPath([string]$registry.InstallLocation).TrimEnd('\')
      $ownedPrefix = [System.IO.Path]::GetFullPath($scratch).TrimEnd('\') + '\'
      if ($location.StartsWith($ownedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $uninstaller = Join-Path $location "unins000.exe"
        if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
          $cleanup = Start-NativeProcess -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -WorkingDirectory $scratch
          [void]$cleanup.WaitForExit(120000)
        }
        if (Test-Path -LiteralPath $uninstallKey) {
          Remove-Item -LiteralPath $uninstallKey -Recurse -Force
        }
      } else {
        $cleanupFailed = $true
        if ($null -eq $cleanupErrorCode) { $cleanupErrorCode = "cleanup_install_boundary" }
      }
    }
  } catch {
    $cleanupFailed = $true
    if ($null -eq $cleanupErrorCode) { $cleanupErrorCode = "cleanup_install" }
  }
  try { Remove-OwnedScratch -Scratch $scratch -TempRoot $tempRoot } catch {
    $cleanupFailed = $true
    if ($null -eq $cleanupErrorCode) { $cleanupErrorCode = "cleanup_scratch" }
  }
  $currentEnvironment = [System.Environment]::GetEnvironmentVariables("Process")
  foreach ($name in $currentEnvironment.Keys) {
    if (-not $originalEnvironment.ContainsKey($name)) {
      [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
  }
  foreach ($name in $originalEnvironment.Keys) {
    [System.Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
  }
}

if ($cleanupFailed -and $evidence.ok) {
  $evidence = [ordered]@{
    schema_version = "paper-reader-clean-smoke-evidence-v1"
    ok = $false
    release_manifest_sha256 = $ExpectedManifestSha256
    harness_sha256 = $harnessSha256
    previous_release_manifest_sha256 = $previousManifestForEvidence
    error_code = if ($null -ne $cleanupErrorCode) { $cleanupErrorCode } else { "cleanup_failed" }
  }
}
Write-CleanSmokeEvidence -Evidence $evidence -Path $evidencePath
if (-not $evidence.ok) {
  exit 1
}
