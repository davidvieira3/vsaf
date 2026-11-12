# Inventaire infra — VMware vSphere & Proxmox VE

Application web d'inventaire façon RVTools, multi-vCenter et multi-cluster Proxmox, avec authentification locale ou Active Directory (LDAPS, plusieurs domaines) et une administration entièrement faite depuis le navigateur.

## Architecture

```
navigateur ──HTTPS──> nginx ──> web (Django + gunicorn) ──> PostgreSQL
                                     │                         ▲
                                     └── Redis (file) ──> worker(s) Celery ──┘ ──> vCenter / Proxmox / OME / OneView / BMC
                                                       ▲
                                              beat (planificateur, toutes les minutes)
```

Chaque source (vCenter ou cluster Proxmox) est une tâche indépendante. Avec `COLLECT_CONCURRENCY=16`, un worker collecte 16 sources en parallèle ; pour 100 vCenter et 1 000 ESXi un seul worker suffit généralement, et `docker compose up -d --scale worker=3` permet d'aller plus loin. La collecte vSphere lit chaque type d'objet en un seul appel au PropertyCollector (comme RVTools), seule la découverte CDP/LLDP fait un appel par hôte.

Chaque collecte remplace l'instantané de sa source dans une transaction : une collecte en échec laisse le dernier inventaire valide en place.

## Ce que montre chaque vue

| Vue | Contenu principal |
|---|---|
| vCenter & Proxmox | produit, version, build, API, nombre de datacenters / clusters / hôtes / VM, cœurs, RAM, vCPU, ratios vCPU/cœur et vRAM/RAM, capacité de stockage, état de la dernière collecte |
| Clusters | hôtes, cœurs, threads, RAM, utilisation CPU/RAM, ratios (VM allumées et toutes), HA, contrôle d'admission, DRS, vSAN, EVC, quorum PVE |
| Hôtes | cluster, IP de gestion, état, maintenance, version/build, constructeur, modèle, n° de série, **BMC (iDRAC/iLO…) : type, IP, firmware**, BIOS, CPU, sockets/cœurs/threads, RAM, ratios, HBA (WWPN/IQN) dans le détail |
| Stockage | type (VMFS, NFS, vSAN, vVol, ZFS, Ceph/RBD, LVM…), version FS, capacité, utilisé, libre, provisionné, % avec seuils, partagé, hôtes, VM, cible NFS/Ceph, extents |
| Commutateurs virtuels | vDS (version, MTU, uplinks, LACP, NIOC, CDP/LLDP, hôtes membres), vSwitch standard, bridges / bonds / OVS / zones SDN Proxmox |
| Portgroups & VLAN | portgroups distribués et standard (VLAN, trunk, PVLAN, teaming, sécurité), VNets SDN et VLAN Linux |
| CDP / LLDP | une ligne par carte physique : MAC, vitesse, vSwitch, switch physique, port, IP du switch, plateforme, VLAN natif |
| Machines virtuelles | état, OS, IP, vCPU, vRAM, disques, réseaux, datastores, Tools/agent, notes, tags, **et depuis ServiceNow : propriétaire, groupe de support, environnement, application, statut CMDB** (lien direct vers le CI dans le détail) |
| Baies | HPE 3PAR / Primera / Alletra 9000 / Alletra MP, HPE Alletra 5000/6000 (Nimble), NetApp ONTAP, Pure FlashArray : modèle, n° de série, version, contrôleurs, capacité utile, utilisé, libre, provisionné, réduction de données |
| Pools & agrégats | CPG, pools Nimble, agrégats ONTAP : RAID, capacité, utilisation, nombre de volumes |
| Volumes & LUN | taille, utilisé, thin, hôtes ou igroups auxquels le volume est présenté, WWN, et **datastores vSphere/Proxmox correspondants** |

Toutes les vues ont : pagination et filtres côté serveur (supportent des dizaines de milliers de lignes), filtre Tout/VMware/Proxmox, filtre par source, recherche globale, filtres par colonne (`>4`, `<=1,2`, `>500G`, `=exact`, `!exclure`), choix des colonnes mémorisé, panneau de détail avec toutes les propriétés, export CSV ou Excel de la vue filtrée. Le menu « Classeur Excel complet » produit un fichier multi-onglets proche d'un export RVTools.

Les ratios sont calculés sur les **cœurs physiques** (pas les threads) et hors templates. Les seuils d'alerte et critiques se règlent dans Paramètres.

## Baies de stockage

| Famille | API | Port par défaut | Compte lecture seule |
|---|---|---|---|
| 3PAR, Primera, Alletra 9000, Alletra MP Block | WSAPI v1 | 443 puis 8080 (3PAR) | rôle « browse » |
| Alletra 5000/6000, Nimble | REST v1 | 5392 | rôle « guest » |
| NetApp ONTAP 9.6+ | REST | 443 | rôle « readonly » |
| Pure FlashArray (Purity 6+) | REST 2.x, jeton API | 443 | utilisateur « readonly » + jeton |

WSAPI doit être démarré sur les 3PAR (`startwsapi`). Le rattachement datastore ↔ baie se fait par l'identifiant NAA des extents VMFS (3PAR/Primera : WWN direct ; ONTAP : `600a0980` + n° de série ; Pure : `624a9370` + n° de série) et par le chemin d'export pour les datastores NFS. Le datastore affiche alors sa baie et son volume, et le volume la liste de ses datastores.

## Historique Excel

Après chaque collecte réussie d'une source ou d'une baie, un classeur Excel de son inventaire est écrit dans `DATA_DIR/history/AAAA/MM/JJ/`. Un classeur complet (toutes les vues) est produit chaque nuit à 23 h 30, et à la demande depuis la page Historique Excel. La page liste les fichiers (filtres par type, nom, dates), permet de les télécharger (tous rôles) et de les supprimer (administrateurs). La purge s'exécute chaque nuit selon la rétention (183 jours par défaut, réglable dans Paramètres, comme la production par collecte ou quotidienne).

Ordre de grandeur pour 100 vCenter collectés toutes les 4 h, 50 000 VM : 30 à 50 Go sur 6 mois.

## ServiceNow (CMDB)

Gestion → ServiceNow : instance, compte d'intégration (rôle `cmdb_read` ou `itil`) ou OAuth client credentials, table (par défaut `cmdb_ci_vmware_instance`), filtre. Lecture seule via l'API Table, toutes les 6 heures et à la demande.

Rapprochement VM ↔ CI, règles essayées dans l'ordre et modifiables :

```
vm_instance_uuid=uuid
bios_uuid=bios_uuid
object_id+vcenter_uuid=moid+vcenter_uuid
name=name
```

Colonnes affichées (`colonne=champ_servicenow`), modifiables pour vos champs personnalisés :

```
owner=owned_by
managed_by=managed_by
support_group=support_group
environment=environment
status=install_status
application=u_application
```

Les champs supplémentaires (company, department, cost_center…) apparaissent dans le détail de la VM. Le bouton Tester lit 5 CI et signale les champs inconnus de la table. Pour Proxmox, ajoutez un connecteur sur la classe utilisée chez vous (souvent `cmdb_ci_vm_instance` ou une classe personnalisée) avec une règle `name=name` ou `vm_inst_id=vm_id`.

## Installation

Cible : VM RHEL 9 (voir le dimensionnement plus bas). Flux sortants : 443 vers les vCenter, baies Pure/ONTAP/Primera, OME, OneView, BMC et ServiceNow ; 8006 vers les Proxmox ; 5392 vers les Nimble/Alletra 6000 ; 8080 vers les 3PAR ; 636/3269 vers les contrôleurs de domaine ; 22 vers les nœuds Proxmox si l'enrichissement SSH est activé.

```bash
sudo DATA_DISK=/dev/sdb ./scripts/install-rhel9.sh   # Docker, LVM du disque de données, pare-feu
cp .env.example .env
make keys            # copier les deux valeurs dans .env
vi .env              # mots de passe PostgreSQL et admin, nom d'hôte
make cert CN=inventaire.euro.net.intra   # ou déposez server.crt / server.key de votre PKI dans nginx/certs/
make migrations      # une seule fois : génère les migrations dans le dépôt, à commiter
make build up
```

Ouvrez `https://<serveur>/`, connectez-vous avec le compte de `ADMIN_USERNAME`. Tout le reste se fait dans l'interface.

Derrière un proxy d'entreprise, passez les miroirs internes à la construction (`PIP_INDEX_URL`, `VENDOR_BASE` dans `.env`). La bibliothèque de grille est téléchargée au build et servie localement ; les postes utilisateurs n'appellent aucun CDN.

**Sauvegardez `APP_ENCRYPTION_KEY`** avec la base : elle chiffre les mots de passe des vCenter, Proxmox, AD et BMC.

## Mise en route dans l'interface

1. **Paramètres** : collez vos CA internes (PEM) pour vérifier les certificats vCenter/Proxmox/OME, réglez les seuils.
2. **Active Directory** : un domaine par entrée (euro.net.intra, gaia.net.intra, xmp.net.intra). Indiquez les DC, le compte de service, la base de recherche, puis les groupes qui donnent les rôles Administrateur / Opérateur / Lecture (DN complet ou simple CN). Le bouton Tester vérifie le bind et montre le rôle qu'obtiendrait un utilisateur donné. Les groupes imbriqués sont résolus (`LDAP_MATCHING_RULE_IN_CHAIN`) ; pour des groupes d'un autre domaine de la forêt, utilisez le catalogue global en 3269.
3. **Sources** : ajoutez-les une par une ou collez un CSV dans « Importer en masse » (100 vCenter en une fois). « Enregistrer et tester » valide la connexion immédiatement. La première collecte part dans la minute.
4. **Matériel** : ajoutez Dell OpenManage Enterprise, HPE OneView, ou une liste de BMC en Redfish direct. Les IP iDRAC/iLO sont rapprochées des hôtes par numéro de série.

Les utilisateurs se connectent avec `EURO\jdupont`, `jdupont@euro.net.intra`, ou en choisissant l'annuaire dans la liste. Les comptes locaux restent disponibles comme accès de secours.

### Rôles

| Rôle | Peut |
|---|---|
| Lecture | consulter toutes les vues, exporter |
| Opérateur | + lancer des collectes, tester les connexions, synchroniser le matériel |
| Administrateur | + gérer sources, connecteurs, domaines AD, utilisateurs, paramètres, journal d'audit, état des workers |

Toutes les actions sont tracées dans le journal d'audit.

## Comptes en lecture seule

**vCenter** : créez un compte de service (SSO ou AD), puis Administration → Autorisations globales → rôle **Lecture seule**, « Propager aux enfants ». Ce rôle suffit pour tout l'inventaire, CDP/LLDP compris (la méthode utilisée exige seulement le privilège System.Read).

**Proxmox VE** (dans l'interface Proxmox, sans SSH) : Datacenter → Permissions → Utilisateurs → ajouter `inventaire@pve` ; onglet Jetons API → ajouter un jeton `collecte` (séparation des privilèges cochée) ; Permissions → Ajouter → Permission de jeton API → chemin `/`, rôle **PVEAuditor**, propager. Dans l'application : compte `inventaire@pve`, id du jeton `collecte`, secret = valeur du jeton.

**Proxmox, n° de série / BMC / LLDP** : l'API Proxmox ne les expose pas. Option « enrichissement SSH » : l'application exécute elle-même `dmidecode`, `ipmitool lan print` et `lldpctl -f json` sur chaque nœud. Prévoir `apt install lldpd ipmitool` sur les nœuds et un compte dédié avec sudo limité :
```
inventaire ALL=(root) NOPASSWD: /usr/sbin/dmidecode, /usr/bin/ipmitool, /usr/sbin/lldpctl
```
Sans cette option, les nœuds Proxmox n'ont pas de numéro de série : les connecteurs OME / OneView / Redfish ne peuvent donc pas leur rattacher de BMC.

**Dell OME** : un compte « Viewer ». **HPE OneView** : rôle « Read only ». **Redfish direct** : un compte lecture seule sur chaque BMC (rôle ReadOnly / Operator selon le constructeur).

## Dimensionnement de la VM RHEL (100 vCenter, 1 000 ESXi, ~50 000 VM)

| Élément | Valeur |
|---|---|
| OS | RHEL 9.x minimal, compatibilité matérielle VM 19+ (ESXi 7.0 U2+) |
| vCPU | 8 (1 socket × 8 cœurs) |
| RAM | 32 Go, sans réservation obligatoire |
| Disque 1, OS | 80 Go thin, PVSCSI : / 30 Go, /var 40 Go (images Docker), swap 4 Go |
| Disque 2, données | 200 Go thin, PVSCSI 2e contrôleur, LVM XFS sur /srv/inventaire (PostgreSQL ~10 Go, historique Excel 30 à 50 Go, marge) |
| Réseau | 1 vNIC VMXNET3 dans le VLAN d'administration |
| Paramètres | `COLLECT_CONCURRENCY=16`, `PG_SHARED_BUFFERS=4GB`, `WEB_WORKERS=4` |

Répartition mémoire : 16 collectes simultanées ≈ 8 à 10 Go au pic, PostgreSQL 4 Go + cache, génération du classeur nocturne 1 à 2 Go, web et Redis ~2 Go. Au-delà (200 vCenter, collectes horaires), passez à 12 vCPU / 48 Go et `COLLECT_CONCURRENCY=24`.

## Exploitation

- Historique et journal de chaque collecte : Gestion → Collectes (journal en direct pendant l'exécution).
- État des workers et file d'attente : Gestion → Système.
- Purge automatique de l'historique selon la rétention définie dans Paramètres.
- Mise à jour applicative : `git pull && make build up` (les migrations s'appliquent au démarrage).
- Logs : `make logs`.

## Limites connues et pistes

- Développé et relu sans accès à un vCenter ni à un Proxmox réels : prévoir un premier passage sur un vCenter de recette et ajuster si un champ manque sur une version ancienne (6.7).
- Les clés d'hôtes SSH Proxmox sont acceptées au premier contact (AutoAddPolicy) ; à durcir si nécessaire.
- Pas d'historisation des inventaires (seulement le dernier instantané) : un modèle de snapshots quotidiens permettrait des courbes de tendance.
- Pistes faciles : vue Snapshots VM, vue vSAN, licences vCenter, tags vSphere (API REST), vue Ceph côté Proxmox, alertes e-mail sur seuils.

## Arborescence

```
config/        réglages Django, Celery, URLs
core/          chiffrement des secrets, CA internes, paramètres, audit
accounts/      connexion locale + AD LDAPS multi-domaines, rôles, gestion des comptes et domaines
inventory/
  collectors/  vsphere.py (pyVmomi), proxmox.py (proxmoxer), proxmox_ssh.py, base.py (ratios, persistance)
  hardware/    ome.py, oneview.py, redfish.py, sync.py (rapprochement par n° de série)
  storage/     hpe_wsapi.py, hpe_nimble.py, netapp.py, pure.py, sync.py (rattachement datastores)
  cmdb.py      ServiceNow : lecture, rapprochement, recopie sur les VM
  history.py   classeurs Excel horodatés, rétention, purge
  grids.py     définition des vues et moteur de filtres/tri/pagination
  tasks.py     tâches Celery (collecte, planification, purge)
templates/ static/   interface
docker/ nginx/       déploiement
scripts/             préparation RHEL 9, publication GitHub
```
