#!/usr/bin/env bash
# Préparation d'une VM RHEL 9 pour l'application (à lancer en root, une seule fois).
# Hypothèse : disque de données /dev/sdb (VMDK séparé sur contrôleur PVSCSI). Adaptez DATA_DISK.
set -euo pipefail
DATA_DISK="${DATA_DISK:-/dev/sdb}"
DATA_DIR="${DATA_DIR:-/srv/inventaire}"

echo "== Paquets de base"
dnf -y install dnf-plugins-core git openssl lvm2 open-vm-tools chrony firewalld
systemctl enable --now vmtoolsd chronyd firewalld

echo "== Docker Engine (dépôt Docker pour RHEL ; remplacez par votre miroir Artifactory si besoin)"
dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

echo "== Disque de données en LVM sur ${DATA_DISK}"
if ! vgs vg_data >/dev/null 2>&1; then
  pvcreate "${DATA_DISK}"
  vgcreate vg_data "${DATA_DISK}"
  lvcreate -n lv_inventaire -l 100%FREE vg_data
  mkfs.xfs /dev/vg_data/lv_inventaire
  mkdir -p "${DATA_DIR}"
  echo "/dev/vg_data/lv_inventaire ${DATA_DIR} xfs defaults,noatime 0 0" >> /etc/fstab
  mount "${DATA_DIR}"
fi
mkdir -p "${DATA_DIR}/pgdata" "${DATA_DIR}/history"
chown 1000:1000 "${DATA_DIR}/history"

echo "== Pare-feu : HTTPS (et HTTP pour la redirection)"
firewall-cmd --permanent --add-service=https --add-service=http
firewall-cmd --reload

echo "== Réglages noyau conseillés pour PostgreSQL et Redis"
cat > /etc/sysctl.d/90-inventaire.conf <<'SYS'
vm.swappiness = 10
vm.overcommit_memory = 1
SYS
sysctl --system >/dev/null

echo "Terminé. Suite : cloner le dépôt, cp .env.example .env, make keys / cert / build up (voir README)."
