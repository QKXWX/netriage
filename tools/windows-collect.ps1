param([string]$OutFile = "")

# netriage · Windows 采集器
#
# 读取 netsh 无线接口（信号/速率/频段/信道）+ Windows 自身状态（机型/BIOS/运行时长/CPU 负载/内存），
# 输出可直接导入 netriage 页面 02「导入 JSON」的结构。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\windows-collect.ps1 -OutFile wifi.json
#   powershell -ExecutionPolicy Bypass -File tools\windows-collect.ps1        # 直接打印
#
# 只读：netsh wlan show interfaces / Get-CimInstance，不改任何设置。

$ErrorActionPreference = 'SilentlyContinue'

function Get-Field([string]$text, [string]$pattern) {
    $m = [regex]::Match($text, $pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return $null
}

$netshText = ((netsh wlan show interfaces) -join "`n")
$wifi = [ordered]@{}
$ssid = Get-Field $netshText '^\s*(?:SSID|SSID 名称)\s*:\s*(.+)$'
$state = Get-Field $netshText '^\s*(?:State|状态)\s*:\s*(.+)$'
$signalPct = Get-Field $netshText '^\s*(?:Signal|信号)\s*:\s*([0-9]+)%'
$rxRate = Get-Field $netshText '^\s*(?:Receive rate|接收速率)[^:]*:\s*([0-9.]+)'
$txRate = Get-Field $netshText '^\s*(?:Transmit rate|传输速率)[^:]*:\s*([0-9.]+)'
$bandRaw = Get-Field $netshText '^\s*(?:Band|频段)\s*:\s*(.+)$'
$channel = Get-Field $netshText '^\s*(?:Channel|信道)\s*:\s*([0-9]+)'
$radio = Get-Field $netshText '^\s*(?:Radio type|无线电类型)\s*:\s*(.+)$'

if ($ssid) { $wifi['ssid'] = $ssid }
if ($state) { $wifi['state'] = $state }
if ($signalPct) {
    $pct = [double]$signalPct
    $wifi['signal_pct'] = $pct
    $wifi['rssi'] = [math]::Round(($pct / 2) - 100)
    $wifi['rssi_estimated'] = $true
}
if ($txRate) { $wifi['tx_rate'] = [double]$txRate }
if ($rxRate) { $wifi['rx_rate'] = [double]$rxRate }
if ($channel) { $wifi['channel'] = [int]$channel }
if ($radio) { $wifi['radio'] = $radio }
if ($bandRaw) {
    if ($bandRaw -match '2\.4') { $wifi['band'] = '2.4G' }
    elseif ($bandRaw -match '5|6') { $wifi['band'] = '5G' }
    else { $wifi['band'] = $bandRaw }
}

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
$cpu = Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum
$loadPct = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$cores = [int]($cpu.Sum)
if ($cores -lt 1) { $cores = 1 }

$uptime = 0
if ($os.LastBootUpTime) { $uptime = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalSeconds) }

$system = [ordered]@{
    model        = ("$($cs.Manufacturer) $($cs.Model)" -replace '\s+', ' ').Trim()
    firmware     = $bios.SMBIOSBIOSVersion
    uptime_s     = $uptime
    cores        = $cores
    load1        = [math]::Round(($loadPct / 100.0) * $cores, 2)
    mem_total_mb = [math]::Round($os.TotalVisibleMemorySize / 1024)
    mem_free_mb  = [math]::Round($os.FreePhysicalMemory / 1024)
}

$note = 'Windows 采集：RSSI 由 netsh 信号百分比估算（dBm ≈ 百分比/2 - 100）；load1 = CPU 瞬时占用 × 核数，用于和 netriage 的「负载/核数」口径对齐。'

$payload = [ordered]@{
    source       = 'windows'
    generated_at = (Get-Date).ToString('o')
    device       = $env:COMPUTERNAME
    wifi         = $wifi
    system       = $system
    note         = $note
}

$json = $payload | ConvertTo-Json -Depth 6
if ($OutFile) {
    $full = [System.IO.Path]::GetFullPath($OutFile)
    [System.IO.File]::WriteAllText($full, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "已写入 $full"
} else {
    $json
}
