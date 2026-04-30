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

# TODO: Check if it's already installed.
# Good tutorial:
# https://eecs280staff.github.io/tutorials/setup_wsl.html

# Enable and install Windows Subsystem for Linux (WSL)
Write-Host "Setting up Windows Subsystem for Linux (WSL)..."
wsl --install
Write-Host "WSL setup complete."

# TODO: We want to (only?) install `uv` within WSL, not Windows!
# Install UV package manager from Astral.sh
# Install Visual Studio Code if not already installed
# if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
#     Write-Host "Installing UV package manager from Astral.sh..."
#     irm https://astral.sh/uv/install.ps1 | iex
# } else {
#     Write-Host "UV package manager is already installed."
# }


# Install Git if not already installed
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git not found. Installing..."
    winget install --id Git.Git -e --source winget
    Write-Host "Git installed successfully."
} else {
    Write-Host "Git is already installed."
}

Write-Host "Setup complete!"
