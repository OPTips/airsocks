# AirSocks

Conteneur Docker qui se connecte à un serveur VPN via WireGuard et expose un proxy SOCKS5 sur le port 8080. Conçu pour être utilisé comme passerelle par d'autres applications afin de sécuriser leur trafic internet.

Compatible avec tout fournisseur VPN proposant des configurations WireGuard standard (AirVPN, Mullvad, ProtonVPN, etc.).

## Fonctionnement

- Sélectionne aléatoirement un fichier de configuration WireGuard au démarrage et à chaque reconnexion
- Vérifie périodiquement l'état de la connexion VPN (handshake WireGuard)
- **Kill switch** : si le VPN tombe, le proxy est coupé immédiatement et les règles iptables bloquent tout trafic sortant — aucune fuite n'est possible via l'interface réseau normale
- Se reconnecte automatiquement avec une configuration aléatoire différente

## Prérequis

- Docker avec Docker Compose
- Des configurations WireGuard (`.conf`) fournies par votre fournisseur VPN

## Installation

**1. Récupérer les configurations WireGuard**

Télécharger des fichiers `.conf` WireGuard depuis votre fournisseur VPN et les placer dans le dossier `configs/` :

```
configs/
├── vpn-paris-1.conf
├── vpn-zurich-2.conf
└── ...
```

**2. Démarrer**

```bash
docker compose up --build -d
```

**3. Vérifier**

```bash
# Voir les logs
docker compose logs -f

# Vérifier que le trafic passe par le VPN
curl --socks5-hostname localhost:8080 https://ifconfig.me
# → doit afficher l'IP du serveur VPN, pas votre IP réelle
```

## Utilisation du proxy

Le proxy SOCKS5 est accessible sur `localhost:8080` (ou l'IP du conteneur depuis d'autres conteneurs).

**Firefox** : `Paramètres → Général → Paramètres réseau → Configuration manuelle`
- Hôte SOCKS : `localhost` — Port : `8080` — **SOCKS v5**
- Cocher **"Utiliser le DNS distant pour SOCKS v5"**

**Curl** :
```bash
curl --socks5-hostname localhost:8080 https://example.com
```

**Autre conteneur Docker** (même réseau) :
```yaml
environment:
  - ALL_PROXY=socks5h://airsocks:8080
```

## Configuration

Les paramètres sont définis dans `compose.yml` via des variables d'environnement :

| Variable | Défaut | Description |
|---|---|---|
| `CHECK_INTERVAL` | `30` | Secondes entre deux vérifications VPN |
| `MAX_HANDSHAKE_AGE` | `180` | Âge max du handshake WireGuard avant reconnexion |
| `MAX_RECONNECT_ATTEMPTS` | `5` | Nombre de tentatives avant abandon |
| `RECONNECT_DELAY` | `10` | Délai de base entre les tentatives (multiplié par le numéro de tentative) |
| `LOG_LEVEL` | `INFO` | Niveau de log Python (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `CONNECTIVITY_CHECK_HOST` | `1.1.1.1` | IP pingée à travers le tunnel pour vérifier la connectivité |

## Kill switch

Le kill switch fonctionne sur deux niveaux :

1. **Applicatif** : le proxy microsocks est arrêté dès qu'une anomalie VPN est détectée
2. **Réseau** : des règles iptables bloquent tout trafic sortant qui ne passe pas par l'interface WireGuard (`wg0`), même si le proxy tournait encore

Le trafic autorisé hors tunnel se limite aux paquets UDP vers le serveur VPN (nécessaire pour que WireGuard puisse se reconnecter).

## Notes

- IPv6 est désactivé dans le conteneur ; si vos configurations WireGuard incluent des adresses IPv6, elles sont retirées automatiquement
- Les directives `DNS` et `PostUp`/`PostDown` sont retirées des configs avant usage — le DNS reste sécurisé car tout le trafic transite par le tunnel (`AllowedIPs = 0.0.0.0/0`)
- Le conteneur nécessite les capabilities `NET_ADMIN` et `SYS_MODULE` ainsi que l'accès à `/dev/net/tun`
