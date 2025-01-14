# Ensure script is run as Administrator
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as an administrator."; exit 1
}

# Install Visual Studio Code if not already installed
if (-not (Get-Command code -ErrorAction SilentlyContinue)) {
    Write-Host "Visual Studio Code not found. Installing..."
    $installerUrl = "https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user"
    $installerPath = "$env:TEMP\VSCodeSetup.exe"

    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
    Start-Process -FilePath $installerPath -ArgumentList "/silent" -Wait
    Remove-Item $installerPath
    Write-Host "Visual Studio Code installed successfully."
} else {
    Write-Host "Visual Studio Code is already installed."
}

# Enable and install Windows Subsystem for Linux (WSL)
Write-Host "Setting up Windows Subsystem for Linux (WSL)..."
wsl --install
Write-Host "WSL setup complete. Restart your machine if required."

# Install UV package manager from Astral.sh
Write-Host "Installing UV package manager from Astral.sh..."
# todo: Can probably just run `irm ... | iex` since we are already in a script?
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Install Git if not already installed
# TODO: This errors out, probably because TEMP isn't set.
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git not found. Installing..."
    $gitInstallerUrl = "https://github.com/git-for-windows/git/releases/latest/download/Git-2.40.1-64-bit.exe"
    $gitInstallerPath = "$env:TEMP\GitSetup.exe"
    Invoke-WebRequest -Uri $gitInstallerUrl -OutFile $gitInstallerPath -UseBasicParsing
    Start-Process -FilePath $gitInstallerPath -ArgumentList "/VERYSILENT" -Wait
    Remove-Item $gitInstallerPath
    Write-Host "Git installed successfully."
} else {
    Write-Host "Git is already installed."
}

Write-Host "Setup complete!"
