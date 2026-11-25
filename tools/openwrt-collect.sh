#!/bin/sh
# netriage · OpenWrt 采集器
#
# 在路由器上把「PPPoE 拨号日志 + 无线信号」导成一份 JSON，
# 拿到 netriage 页面 02「现场数据」的「导入 JSON」里直接喂进去。
#
# 用法：
#   sh openwrt-collect.sh                 # 自动选第一个无线设备，WAN 接口 wan
#   sh openwrt-collect.sh phy0-ap0        # 指定无线设备
#   sh openwrt-collect.sh phy0-ap0 pppoe-wan
#   RX_POWER=-24.6 sh openwrt-collect.sh  # 光猫光功率（dBm），可选
#
# 只读：logread / iwinfo / date，不改任何配置。

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
printf '  "dial_log": "%s"\n' "$(printf '%s' "$DIAL" | jesc)"
printf '}\n'
