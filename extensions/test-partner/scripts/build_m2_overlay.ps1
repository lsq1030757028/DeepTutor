param(
    [Parameter(Mandatory = $false)]
    [string]$ImageTag = "deeptutor:p3-full-m2",

    [Parameter(Mandatory = $false)]
    [switch]$SelfTest,

    [Parameter(Mandatory = $false)]
    [switch]$SkipWebBuild
)

$ErrorActionPreference = "Stop"

function Assert-SafeTemporaryContext {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$TempRoot,

        [Parameter(Mandatory = $false)]
        [switch]$RequireExisting
    )

    $rootFull = [IO.Path]::GetFullPath($TempRoot).TrimEnd(
        [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    )
    $pathFull = [IO.Path]::GetFullPath($Path).TrimEnd(
        [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    )
    $parent = [IO.Directory]::GetParent($pathFull)
    $leaf = [IO.Path]::GetFileName($pathFull)

    if ($null -eq $parent -or
        -not $parent.FullName.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase) -or
        $leaf -notmatch '^deeptutor-m2overlay-[0-9a-f]{32}$') {
        throw "Refusing unsafe temporary build context: $pathFull"
    }

    if ($RequireExisting) {
        $item = Get-Item -LiteralPath $pathFull -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point temporary build context: $pathFull"
        }
        $pathFull = [IO.Path]::GetFullPath($item.FullName)
        $resolvedParent = [IO.Directory]::GetParent($pathFull)
        if ($null -eq $resolvedParent -or
            -not $resolvedParent.FullName.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Resolved temporary build context escaped its root: $pathFull"
        }
    }

    return $pathFull
}

function Copy-DirectoryTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $robocopy = Get-Command robocopy.exe -ErrorAction SilentlyContinue
    if ($null -ne $robocopy) {
        # Copy-Item still trips MAX_PATH on Next's nested route manifests under
        # long clean-worktree paths. Robocopy is the native directory-tree copy
        # primitive on Windows; exit codes 0..7 are all successful outcomes.
        & $robocopy.Source $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
        $copyExitCode = $LASTEXITCODE
        if ($copyExitCode -gt 7) {
            throw "robocopy failed with exit code ${copyExitCode}: $Source"
        }
        return
    }

    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd(
    [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
)

if ($SelfTest) {
    $safe = Join-Path $tempRoot ("deeptutor-m2overlay-" + ("a" * 32))
    if ((Assert-SafeTemporaryContext -Path $safe -TempRoot $tempRoot) -ne [IO.Path]::GetFullPath($safe)) {
        throw "Safe temporary context was not normalized as expected"
    }
    foreach ($unsafe in @(
        $tempRoot,
        (Join-Path $tempRoot "nested\deeptutor-m2overlay-$('b' * 32)"),
        ([IO.Path]::GetFullPath($tempRoot + "-sibling\deeptutor-m2overlay-$('c' * 32)")),
        (Join-Path $tempRoot "not-our-context")
    )) {
        $rejected = $false
        try {
            Assert-SafeTemporaryContext -Path $unsafe -TempRoot $tempRoot | Out-Null
        }
        catch {
            $rejected = $true
        }
        if (-not $rejected) {
            throw "Unsafe temporary context was accepted: $unsafe"
        }
    }
    Write-Output "build_m2_overlay self-test PASS"
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
$webRoot = Join-Path $repoRoot "web"

if (-not $SkipWebBuild) {
    Push-Location $webRoot
    try {
        # Windows PowerShell 5 会把 native stderr（Next 的 workspace/browser 数据警告）
        # 升格为 NativeCommandError；脚本全局 Stop 时会在真正 exit code 产生前中断。
        # 对 native 命令只认进程退出码，警告仍原样输出，不把 stderr 当构建失败。
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & npm.cmd run build
        $npmExitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedErrorActionPreference
        if ($npmExitCode -ne 0) {
            throw "npm run build failed with exit code $npmExitCode"
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Warning "Skipping web rebuild by explicit request; caller owns build freshness evidence."
}

$requiredServerFilesPath = Join-Path $repoRoot "web\.next\required-server-files.json"
if (-not (Test-Path -LiteralPath $requiredServerFilesPath)) {
    throw "Missing Next required-server-files manifest: $requiredServerFilesPath"
}
$requiredServerFiles = Get-Content -LiteralPath $requiredServerFilesPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
$manifestAppDir = [string]$requiredServerFiles.appDir
if ([string]::IsNullOrWhiteSpace($manifestAppDir) -or
    [IO.Path]::GetFullPath($manifestAppDir) -ne [IO.Path]::GetFullPath($webRoot)) {
    throw "Next standalone manifest does not belong to this web root: $manifestAppDir"
}
$relativeAppDir = [string]$requiredServerFiles.relativeAppDir
$standaloneBase = [IO.Path]::GetFullPath((Join-Path $repoRoot "web\.next\standalone"))
if ([string]::IsNullOrWhiteSpace($relativeAppDir)) {
    # Next 16.3 emits the app directly at standalone/ and leaves
    # relativeAppDir empty. The manifest's absolute appDir check above keeps
    # this fail-closed: an unrelated or stale build cannot select this root.
    $standaloneRoot = $standaloneBase
}
else {
    if ([IO.Path]::IsPathRooted($relativeAppDir)) {
        throw "Unsafe relativeAppDir in required-server-files.json: $relativeAppDir"
    }
    $standaloneRoot = [IO.Path]::GetFullPath((Join-Path $standaloneBase $relativeAppDir))
    if (-not $standaloneRoot.StartsWith(
        $standaloneBase + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Next standalone root escaped its build output: $standaloneRoot"
    }
}
$journeyRoute = Join-Path $standaloneRoot ".next\server\app\(workspace)\test-journey"

if (-not (Test-Path -LiteralPath (Join-Path $standaloneRoot "server.js"))) {
    throw "Missing host-built Next standalone root: $standaloneRoot"
}
if (-not (Test-Path -LiteralPath $journeyRoute)) {
    throw "Host build does not contain the test-journey route: $journeyRoute"
}

$context = Join-Path $tempRoot ("deeptutor-m2overlay-" + [Guid]::NewGuid().ToString("N"))
$contextFull = Assert-SafeTemporaryContext -Path $context -TempRoot $tempRoot

try {
    New-Item -ItemType Directory -Path (Join-Path $context "web\.next") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $context "extensions\test-partner") -Force | Out-Null

    Copy-Item -LiteralPath (Join-Path $repoRoot "deeptutor") -Destination (Join-Path $context "deeptutor") -Recurse -Force
    $normalizedStandaloneParent = Join-Path $context "web\.next\standalone\DeepTutor"
    New-Item -ItemType Directory -Path $normalizedStandaloneParent -Force | Out-Null
    Copy-DirectoryTree -Source $standaloneRoot -Destination (Join-Path $normalizedStandaloneParent "web")
    Copy-Item -LiteralPath (Join-Path $repoRoot "web\.next\static") -Destination (Join-Path $context "web\.next\static") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "web\public") -Destination (Join-Path $context "web\public") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "extensions\test-partner\server") -Destination (Join-Path $context "extensions\test-partner\server") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "extensions\test-partner\skills") -Destination (Join-Path $context "extensions\test-partner\skills") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "Dockerfile.m2overlay") -Destination (Join-Path $context "Dockerfile") -Force

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker.exe build -t $ImageTag -f (Join-Path $context "Dockerfile") $context
    $dockerExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($dockerExitCode -ne 0) {
        throw "docker build failed with exit code $dockerExitCode"
    }
}
finally {
    if (Test-Path -LiteralPath $contextFull) {
        $resolvedContext = Assert-SafeTemporaryContext -Path $contextFull -TempRoot $tempRoot -RequireExisting
        Remove-Item -LiteralPath $resolvedContext -Recurse -Force
    }
}
