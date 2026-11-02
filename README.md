# SUP_KPI : supervision des erreurs de logs

Une page web qui montre **toutes les erreurs de tous tes serveurs au même endroit**, avec des chiffres (KPI),
des graphiques, des voyants de couleur et un **bilan de la veille** pour chaque application.

---

## 🧸 Comment ça marche ? (expliqué simplement)

Imagine une école avec plein de classes (les **serveurs**). Dans chaque classe, il y a des élèves
(les **applications**) qui écrivent dans leur cahier (les **fichiers de log**).

- Dans chaque classe, on met un **petit robot** (l'**agent**). Toutes les minutes, il lit les
  nouvelles lignes des cahiers et recopie celles qui contiennent un mot interdit
  (`ERROR`, `FATAL`, `KO`… ce sont les **patterns**).
- Le robot envoie ces lignes au **directeur** (le **central**).
- Le directeur range tout et affiche un grand **tableau** (la **page web**) où tu vois tout d'un coup d'œil.

```
 Serveur 1 ─ robot ─┐
 Serveur 2 ─ robot ─┼──►  Central (base de données + page web)  ──►  ton navigateur
 Serveur N ─ robot ─┘
```

Rien à installer à part **Python 3**, qui est déjà présent sur presque tous les serveurs Linux.
Pas besoin d'internet.

---

## 🖥️ Ce que tu vois sur la page web

### Onglet « Tableau de bord »
- **Erreurs sur la période** (1 h, 24 h, 7 jours ou 30 jours) et si ça monte ↑ ou descend ↓.
- **Voyants des serveurs** :

  | Voyant | Ça veut dire |
  |---|---|
  | ● vert | Aucune erreur dans la dernière heure |
  | ■ orange | Quelques erreurs dans la dernière heure (dès 1) |
  | ▲ rouge | Beaucoup d'erreurs dans la dernière heure (dès 20) |
  | ○ gris « muet » | Le robot n'a rien envoyé depuis 5 minutes : serveur éteint ou robot arrêté ? |

- **Graphique** de l'évolution des erreurs dans le temps (passe la souris dessus pour voir les chiffres).
- **Erreurs par serveur** et **par application** (clique sur une barre pour filtrer).
- **Top 10** des erreurs qui reviennent le plus souvent (les erreurs presque identiques sont regroupées).
- **Dernières erreurs** : les vraies lignes de log, avec une recherche par mot.

### Onglet « Bilan de la veille »
Un tableau avec **toutes les applications de tous les serveurs** pour la journée d'hier :
état, nombre d'erreurs, comparaison avec avant-hier, heure de la 1re et de la dernière erreur,
erreur la plus fréquente. Les applications sans erreur apparaissent aussi (en vert), et celles dont le
robot n'a rien envoyé sont marquées « Pas de nouvelles ».
Tu peux choisir un autre jour et **exporter pour Excel** (bouton CSV).

### Onglet « Patterns »
La liste des mots recherchés. Tu peux **ajouter** un mot (ex. `Timeout`), le **désactiver** ou le **supprimer**.
Les robots reçoivent la nouvelle liste **tout seuls** en moins d'une minute.
Les majuscules/minuscules ne comptent pas. Mots présents au départ :
`ERROR`, `FATAL`, `CRITICAL`, `SEVERE`, `Exception` et `KO` (le mot tout seul).

---

## 🚀 Essayer tout de suite sur ton ordinateur (démo)

```sh
sh scripts/demo.sh
```
Puis ouvre **http://localhost:8099** dans ton navigateur. Ça crée de faux serveurs avec de fausses erreurs
(et 8 jours d'historique inventé). `Ctrl + C` pour arrêter.

---

## 🤖 Déploiement automatique avec GitLab + Ansible (méthode conseillée)

Ici, **personne n'a besoin d'être root** : tout est installé et lancé avec l'utilisateur `webadmin`
(dans `/usr/local/web/sup-kpi`). C'est GitLab qui fait le travail quand tu cliques sur un bouton.

### Comment ça marche ?
1. Tu modifies le projet et tu l'envoies sur GitLab.
2. GitLab lance tout seul les **tests** et les **vérifications** (étapes `test` et `lint`).
3. Si tout est vert, deux boutons ▶ apparaissent dans le pipeline :
   - **`deploy:horsprod`** : installe sur les serveurs hors-prod (depuis n'importe quelle branche) ;
   - **`deploy:prod`** : installe sur les serveurs de prod (seulement depuis la branche principale).
4. En cas de souci : boutons **`rollback:horsprod`** / **`rollback:prod`**, qui reviennent à la version d'avant.
   On garde les 3 dernières versions sur chaque serveur. La base de données n'est jamais effacée.

### À faire une seule fois

**a) Demander à un administrateur root**, sur chaque serveur, de lancer :
```sh
loginctl enable-linger webadmin
```
Sans ça, les programmes de webadmin ne redémarrent pas tout seuls (après un redémarrage du serveur, par exemple).
Si c'est oublié, le déploiement s'arrête et le dit clairement.

**b) Créer une clé SSH** pour que GitLab puisse se connecter en tant que webadmin :
```sh
ssh-keygen -t ed25519 -f sup-kpi-deploy -N "" -C "gitlab-sup-kpi"
```
- Le contenu de `sup-kpi-deploy.pub` va dans `/usr/local/web/.ssh/authorized_keys` sur **chaque** serveur.
- Les empreintes des serveurs : `ssh-keyscan srv-hpr-app01 srv-hpr-app02 srv-prd-app01 ... > known_hosts`

**c) Dans GitLab** → *Settings → CI/CD → Variables*, créer :

| Nom | Type | Contenu |
|---|---|---|
| `SSH_PRIVATE_KEY` | **File** | le contenu du fichier `sup-kpi-deploy` (la clé privée) |
| `SSH_KNOWN_HOSTS` | Variable | le contenu du fichier `known_hosts` |

Si tu coches « Protected » (conseillé), ces variables ne marchent que sur les branches protégées
(`main` par exemple) : il faut alors aussi protéger les branches d'où tu déploies le hors-prod.
Conseil : dans *Settings → CI/CD → Protected environments*, protège `prod` pour choisir qui a le droit de cliquer.

**d) Décrire tes serveurs** dans `deploy/ansible/inventories/` (un dossier `horsprod`, un dossier `prod`) :
- `hosts.yml` : la liste des serveurs. Le groupe `central` contient **le** serveur qui affiche la page web,
  le groupe `agents` contient **tous** les serveurs à surveiller.
- `host_vars/<nom-du-serveur>.yml` : l'adresse du serveur et ses applications :
  ```yaml
  ansible_host: 10.10.0.12
  sup_kpi_applications:
    - name: facturation
      log_dir: /usr/local/web/facturation/logs
      file_pattern: "*.log"
  ```
- `group_vars/all.yml` : les réglages de l'environnement (port, couleurs des voyants, jours d'historique).

Le runner GitLab doit pouvoir joindre les serveurs en SSH (port 22), et les serveurs doivent pouvoir joindre
le central sur le port 8080.

### Où sont les choses sur un serveur ?
```
/usr/local/web/sup-kpi/current   → la version active (lien vers releases/<numéro de commit>)
/usr/local/web/sup-kpi/conf/     → central.conf, agent.conf (générés par Ansible : ne pas modifier à la main)
/usr/local/web/sup-kpi/data/     → la base de données du central
```
Commandes utiles (connecté en webadmin) :
```sh
systemctl --user status sup-kpi-agent        # le robot tourne-t-il ?
journalctl --user -u sup-kpi-agent -f        # ce que fait le robot
systemctl --user restart sup-kpi-central     # relancer la page web
```

---

## 🏠 Installer à la main (avec un accès root)

Si tu n'utilises pas GitLab et que tu as un accès root, tu peux aussi tout installer à la main.
Copie le dossier `SUP_KPI` sur chaque serveur (par exemple avec `git clone` ou `scp`).

### Étape 1 : installer le central (une seule fois, sur le serveur choisi)

```sh
cd SUP_KPI
sudo sh central/install_central.sh
```
C'est tout ! La page est sur **http://ADRESSE-DU-SERVEUR:8080**.
Si tu n'arrives pas à l'ouvrir, il faut sans doute ouvrir le port 8080 dans le pare-feu, par exemple :
`sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload` (Red Hat / CentOS)
ou `sudo ufw allow 8080/tcp` (Ubuntu).

Réglages (port, seuils des couleurs, nombre de jours gardés) : fichier `/etc/sup-kpi/central.conf`,
puis `sudo systemctl restart sup-kpi-central`.

### Étape 2 : installer un robot sur chaque serveur

```sh
cd SUP_KPI
sudo sh agent/install_agent.sh
sudo nano /etc/sup-kpi/agent.conf
```

Dans `agent.conf`, tu changes 3 choses :

```json
{
  "central_url": "http://10.0.0.10:8080",      ← l'adresse du central
  "server_name": "srv-web-01",                  ← le nom affiché sur la page
  "applications": [                             ← une ligne par application
    { "name": "facturation", "log_dir": "/var/log/facturation", "file_pattern": "*.log" },
    { "name": "portail",     "log_dir": "/opt/portail/logs",    "file_pattern": ["*.log", "*.out"] }
  ]
}
```
(Les flèches `←` sont juste des explications : ne les écris pas dans le fichier.)

- `log_dir` : le dossier où sont les logs de l'application.
- `file_pattern` : quels fichiers lire. `*.log` = tous les fichiers qui finissent par `.log`.
  `**/*.log` = aussi dans les sous-dossiers. Tu peux en mettre plusieurs entre `[ ]`.

Puis :
```sh
sudo systemctl restart sup-kpi-agent
```
Le serveur apparaît sur la page en moins d'une minute. 🎉

> Au premier démarrage, le robot commence **à la fin** des fichiers : il ne renvoie pas tout l'ancien
> historique, seulement les nouvelles erreurs. Pour tout relire depuis le début, mets
> `"read_from_start": true` avant le premier démarrage.

---

## 🩺 Ça ne marche pas ?

| Problème | Que faire |
|---|---|
| La page ne s'ouvre pas | `sudo systemctl status sup-kpi-central` et vérifier le pare-feu (port 8080) |
| Un serveur est « muet » | Sur ce serveur : `sudo systemctl status sup-kpi-agent` puis `sudo journalctl -u sup-kpi-agent -f` |
| Le robot dit « Central injoignable » | Vérifier `central_url` et que le serveur peut joindre le central : `curl http://ADRESSE:8080/api/health` |
| Le robot dit « Répertoire introuvable » | Corriger `log_dir` dans `/etc/sup-kpi/agent.conf` |
| Tester le robot une seule fois | `sudo python3 /opt/sup-kpi/agent/agent.py --config /etc/sup-kpi/agent.conf --once` |

Si le central est éteint un moment, les robots **gardent les erreurs de côté** et les renvoient quand il revient.

---

## 🔧 Pour les curieux (partie technique)

- `central/server.py` : serveur web + API JSON + base SQLite (Python 3, bibliothèque standard uniquement).
- `central/static/` : la page web (HTML/CSS/JS sans bibliothèque externe).
- `agent/agent.py` : l'agent. Lecture incrémentale (mémorise la position dans chaque fichier, gère la rotation
  des logs), tampon sur disque si le central ne répond pas.
- Les erreurs de plus de 30 jours sont supprimées automatiquement (`retention_days`).
- La page n'a **pas de mot de passe** : à n'ouvrir que sur le réseau interne.
- API : `POST /api/ingest`, `GET|POST /api/patterns`, `POST /api/patterns/<id>/enable|disable`,
  `DELETE /api/patterns/<id>`, `GET /api/kpi?period=1h|24h|7d|30d&server=&app=`,
  `GET /api/errors?…&pattern=&q=`, `GET /api/daily-report?date=AAAA-MM-JJ`, `GET /api/daily-report.csv`.
- Tests : `python3 -m unittest discover tests`
