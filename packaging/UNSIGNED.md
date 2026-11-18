# Unsigned Windows preview

This paper-reader Windows preview is **not digitally signed**. Windows SmartScreen may show an
unknown-publisher warning. The project does not claim Microsoft approval, Authenticode identity,
or reputation-based trust for this package.

Before running either distribution:

1. obtain every file from the project's GitHub Release page;
2. verify `SHA256SUMS.txt` against the downloaded files with built-in Windows PowerShell:

   ```powershell
   Get-Content .\SHA256SUMS.txt | ForEach-Object {
     $expected, $name = $_ -split '  ', 2
     $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath ".\$name").Hash.ToLowerInvariant()
     if ($actual -ne $expected) { throw "SHA-256 mismatch: $name" }
   }
   ```

3. run `python scripts/build_release_metadata.py verify <release-directory>` from the matching
   source revision when a trusted Python environment is available; and
4. inspect `release-manifest.json`, both CycloneDX SBOMs, and the capability matrix.

A matching SHA-256 proves that bytes match the published release inventory and does not establish publisher trust,
safety, suitability, or the absence of vulnerabilities. Do not bypass an operating
system warning unless you have independently verified the release source and hashes.
