#!/bin/sh
# netriage · OpenWrt 采集器
#
# 在路由器上把「PPPoE 拨号日志 + 无线信号 + 路由自身负载/内存/WAN 错误」导成一份 JSON，
# 拿到 netriage 页面 02「现场数据」的「导入 JSON」里直接喂进去。
#
# 用法：
#   sh openwrt-collect.sh                 # 自动选第一个无线设备，WAN 接口 pppoe-wan
#   sh openwrt-collect.sh phy0-ap0        # 指定无线设备
#   sh openwrt-collect.sh phy0-ap0 pppoe-wan
#   RX_POWER=-24.6 sh openwrt-collect.sh  # 光猫光功率（dBm），可选
#
# 只读：logread / iwinfo / /proc / /sys，不改任何配置。

set -u

WIFI_DEV="${1:-}"
WAN_IF="${2:-pppoe-wan}"

if [ -z "$WIFI_DEV" ]; then
  WIFI_DEV=$(iwinfo 2>/dev/null | sed -n 's/^\([^ ]*\) .*ESSID.*/\1/p' | head -n 1)
fi
if [ -z "$WIFI_DEV" ]; then
  WIFI_DEV=$(iwinfo 2>/dev/null | awk 'NF && $1 !~ /^Usage/ {print $1; exit}')
fi

# ---------- 拨号日志 ----------
DIAL=$(logread -e pppd 2>/dev/null | tail -n 200)
if [ -z "$DIAL" ]; then
  DIAL=$(grep -i pppd /var/log/messages 2>/dev/null | tail -n 200)
fi
if [ -z "$DIAL" ]; then
  DIAL=$(logread 2>/dev/null | grep -iE 'pppd|pppoe|wan' | tail -n 200)
fi

# ---------- 无线信号 ----------
RSSI=""; NOISE=""; RATE=""; CHAN=""; FREQ=""; BAND=""; SNR=""
if [ -n "$WIFI_DEV" ] && command -v iwinfo >/dev/null 2>&1; then
  INFO=$(iwinfo "$WIFI_DEV" info 2>/dev/null)
  RSSI=$(printf '%s\n' "$INFO" | sed -n 's/.*Signal: *\(-*[0-9][0-9]*\) *dBm.*/\1/p' | head -n 1)
  NOISE=$(printf '%s\n' "$INFO" | sed -n 's/.*Noise: *\(-*[0-9][0-9]*\) *dBm.*/\1/p' | head -n 1)
  RATE=$(printf '%s\n' "$INFO" | sed -n 's/.*Bit Rate: *\([0-9][0-9.]*\) *Mbit\/s.*/\1/p' | head -n 1)
  CHAN=$(printf '%s\n' "$INFO" | sed -n 's/.*Channel: *\([0-9][0-9]*\).*/\1/p' | head -n 1)
  FREQ=$(printf '%s\n' "$INFO" | sed -n 's/.*(\([0-9][0-9.]*\) GHz).*/\1/p' | head -n 1)
  SNR=""
  if [ -n "$RSSI" ] && [ -n "$NOISE" ]; then
    SNR=$((RSSI - NOISE))
  fi
  case "$FREQ" in
    2.*) BAND="2.4G" ;;
    [0-9]*) BAND="5G" ;;
  esac
fi

# ---------- 路由自身状态 ----------
jesc() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g' -e 's/\r//g' \
    | awk 'BEGIN{ORS=""} {if (NR > 1) printf "\\n"; printf "%s", $0}'
}

MODEL=$(cat /tmp/sysinfo/model 2>/dev/null)
FIRMWARE=$(awk -F= '/^DISTRIB_DESCRIPTION=/{gsub(/"/, "", $2); print $2}' /etc/openwrt_release 2>/dev/null)
UPTIME=$(cut -d. -f1 /proc/uptime 2>/dev/null)
CORES=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
LOAD=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)
LOAD1=""; LOAD5=""; LOAD15=""
if [ -n "$LOAD" ]; then
  set -- $LOAD
  LOAD1="${1:-}"; LOAD5="${2:-}"; LOAD15="${3:-}"
fi
MEM=$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} /^MemFree:/{f=$2} END{if(a=="")a=f; printf "%d %d", t/1024, a/1024}' /proc/meminfo 2>/dev/null)
MEM_TOTAL=""; MEM_FREE=""
if [ -n "$MEM" ]; then
  set -- $MEM
  MEM_TOTAL="${1:-}"; MEM_FREE="${2:-}"
fi
RXE=$(cat "/sys/class/net/$WAN_IF/statistics/rx_errors" 2>/dev/null)
TXE=$(cat "/sys/class/net/$WAN_IF/statistics/tx_errors" 2>/dev/null)
RXD=$(cat "/sys/class/net/$WAN_IF/statistics/rx_dropped" 2>/dev/null)
TXD=$(cat "/sys/class/net/$WAN_IF/statistics/tx_dropped" 2>/dev/null)

SYS_JSON=""
sys_num() {
  [ -z "$2" ] && return
  if [ -n "$SYS_JSON" ]; then SYS_JSON="$SYS_JSON, "; fi
  SYS_JSON="$SYS_JSON\"$1\": $2"
}
sys_str() {
  [ -z "$2" ] && return
  if [ -n "$SYS_JSON" ]; then SYS_JSON="$SYS_JSON, "; fi
  SYS_JSON="$SYS_JSON\"$1\": \"$(printf '%s' "$2" | jesc)\""
}
sys_str model "$MODEL"
sys_str firmware "$FIRMWARE"
sys_str wan_if "$WAN_IF"
sys_num uptime_s "$UPTIME"
sys_num cores "$CORES"
sys_num load1 "$LOAD1"
sys_num load5 "$LOAD5"
sys_num load15 "$LOAD15"
sys_num mem_total_mb "$MEM_TOTAL"
sys_num mem_free_mb "$MEM_FREE"
sys_num rx_errors "$RXE"
sys_num tx_errors "$TXE"
sys_num rx_dropped "$RXD"
sys_num tx_dropped "$TXD"

# ---------- JSON 输出 -------------
jesc() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g' -e 's/\r//g' \
    | awk 'BEGIN{ORS=""} {if (NR > 1) printf "\\n"; printf "%s", $0}'
}

LQC=""
[ -n "$RSSI" ]  && LQC="$LQC rssi=$RSSI"
[ -n "$SNR" ]   && LQC="$LQC snr=$SNR"
[ -n "$RATE" ]  && LQC="$LQC tx_rate=$RATE"
[ -n "$BAND" ]  && LQC="$LQC band=$BAND"
if [ -n "${RX_POWER:-}" ]; then LQC="$LQC rx_power=$RX_POWER"; fi
LQC=$(printf '%s' "$LQC" | sed 's/^ *//')

WIFI_JSON=""
add_field() {
  # $1=key $2=value(数字)
  [ -z "$2" ] && return
  if [ -n "$WIFI_JSON" ]; then WIFI_JSON="$WIFI_JSON, "; fi
  WIFI_JSON="$WIFI_JSON\"$1\": $2"
}
add_field rssi "$RSSI"
add_field snr "$SNR"
add_field tx_rate "$RATE"
add_field channel "$CHAN"
if [ -n "$BAND" ]; then
  if [ -n "$WIFI_JSON" ]; then WIFI_JSON="$WIFI_JSON, "; fi
  WIFI_JSON="$WIFI_JSON\"band\": \"$BAND\""
fi

printf '{\n'
printf '  "source": "openwrt",\n'
printf '  "generated_at": "%s",\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '  "device": "%s",\n' "$(jesc <<EOF
$WIFI_DEV
EOF
)"
printf '  "lqc": "%s",\n' "$(printf '%s' "$LQC" | jesc)"
printf '  "wifi": {%s},\n' "$WIFI_JSON"
printf '  "system": {%s},\n' "$SYS_JSON"
printf '  "dial_log": "%s"\n' "$(printf '%s' "$DIAL" | jesc)"
printf '}\n'
