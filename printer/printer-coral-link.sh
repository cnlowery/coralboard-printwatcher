#!/bin/bash
# Neptune 4 Pro (Armbian Buster, dhcpcd) <-> CoralBoard over USB (NCM gadget).
#
# What it sets up on the printer:
#   - The kernel's cdc_ncm driver names the board's USB network interface usb0
#     (Synaptics NCM gadget, USB 1d6b:0104). A udev rename to "coral0" didn't take
#     effect on this printer (2026-10-29), so usb0 is used as is.
#   - dhcpcd gives usb0 the static address 192.168.137.1/24 (no gateway)
#     The board is pinned to 192.168.137.2 with gateway 192.168.137.1
#     (/etc/ncm-gadget-setup.machine on the board).
#   - IPv4 forwarding + NAT, so the board reaches the internet through eth0
#   - Port forwards from the printer's LAN address to the board:
#       2222 -> board:22   (ssh -p 2222 root@<printer>)
#       8080 -> board:8080 (for a PrintWatcher web page later)
#
# Usage (as root):  bash printer-coral-link.sh install | uninstall | status
set -u

BOARD=192.168.137.2
LINK=192.168.137.1
WAN=eth0
IF=usb0

UDEV=/etc/udev/rules.d/70-coralboard.rules  # only removed now (left by the first version)
SYSCTL=/etc/sysctl.d/90-coralboard.conf
DHCPCD=/etc/dhcpcd.conf
RULES=/usr/local/sbin/coralboard-nat
UNIT=/etc/systemd/system/coralboard-nat.service
MARK_BEGIN="# >>> coralboard (printer-coral-link.sh)"
MARK_END="# <<< coralboard"

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }

install() {
  rm -f "$UDEV" && udevadm control --reload

  echo "dhcpcd: static $LINK/24 on $IF"
  if ! grep -qF "$MARK_BEGIN" "$DHCPCD"; then
    cp -n "$DHCPCD" "$DHCPCD.before-coralboard"
    cat >> "$DHCPCD" <<EOF

$MARK_BEGIN
interface $IF
static ip_address=$LINK/24
nogateway
noipv6rs
$MARK_END
EOF
  fi

  echo "sysctl: IPv4 forwarding on"
  echo "net.ipv4.ip_forward=1" > "$SYSCTL"
  sysctl -q -p "$SYSCTL"

  echo "iptables rules + boot service"
  cat > "$RULES" <<EOF
#!/bin/sh
# Added by printer-coral-link.sh. Idempotent: each rule is added only if missing.
add() { t=\$1; shift; iptables -t "\$t" -C "\$@" 2>/dev/null || iptables -t "\$t" -A "\$@"; }
add nat POSTROUTING -s 192.168.137.0/24 -o $WAN -j MASQUERADE
add nat PREROUTING -i $WAN -p tcp --dport 2222 -j DNAT --to-destination $BOARD:22
add nat PREROUTING -i $WAN -p tcp --dport 8080 -j DNAT --to-destination $BOARD:8080
add filter FORWARD -i $IF -o $WAN -j ACCEPT
add filter FORWARD -i $WAN -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
add filter FORWARD -i $WAN -o $IF -d $BOARD -p tcp -m multiport --dports 22,8080 -j ACCEPT
EOF
  chmod 755 "$RULES"
  cat > "$UNIT" <<EOF
[Unit]
Description=CoralBoard USB link: NAT and port forwards
After=network.target

[Service]
Type=oneshot
ExecStart=$RULES
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now coralboard-nat.service

  echo "dhcpcd: reload config (eth0 may blink for a second)"
  dhcpcd -n >/dev/null 2>&1 || true
  echo "Done. Plug the board in, then: ssh -p 2222 root@<printer IP>"
  status
}

uninstall() {
  systemctl disable --now coralboard-nat.service 2>/dev/null
  t() { iptables -t "$1" -D "${@:2}" 2>/dev/null; }
  t nat POSTROUTING -s 192.168.137.0/24 -o $WAN -j MASQUERADE
  t nat PREROUTING -i $WAN -p tcp --dport 2222 -j DNAT --to-destination $BOARD:22
  t nat PREROUTING -i $WAN -p tcp --dport 8080 -j DNAT --to-destination $BOARD:8080
  t filter FORWARD -i $IF -o $WAN -j ACCEPT
  t filter FORWARD -i $WAN -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  t filter FORWARD -i $WAN -o $IF -d $BOARD -p tcp -m multiport --dports 22,8080 -j ACCEPT
  rm -f "$UNIT" "$RULES" "$SYSCTL" "$UDEV"
  systemctl daemon-reload
  sed -i "/^$MARK_BEGIN\$/,/^$MARK_END\$/d" "$DHCPCD"
  sysctl -q -w net.ipv4.ip_forward=0
  udevadm control --reload
  dhcpcd -n >/dev/null 2>&1 || true
  echo "Removed."
}

status() {
  echo "--- interface"; ip -br addr show "$IF" 2>/dev/null || echo "$IF not present (board unplugged?)"
  echo "--- forwarding: $(cat /proc/sys/net/ipv4/ip_forward)"
  echo "--- nat"; iptables -t nat -S | grep -E "137|2222|8080"
  echo "--- board ping"; ping -c1 -W1 "$BOARD" >/dev/null 2>&1 && echo "board answers" || echo "no answer from $BOARD"
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  *) echo "Usage: $0 install|uninstall|status"; exit 1 ;;
esac
