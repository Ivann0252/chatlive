# ChatLive — Chat WebSocket temps réel

## Lancer en local

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Ouvre http://localhost:8000

## Déployer sur Render.com (gratuit)

1. Crée un compte sur https://render.com
2. Push ce dossier sur GitHub :
   ```bash
   git init
   git add .
   git commit -m "init chatlive"
   git remote add origin https://github.com/TON_USER/chatlive.git
   git push -u origin main
   ```
3. Sur Render : **New → Web Service → Connect GitHub repo**
4. Render détecte automatiquement `render.yaml`
5. Clique **Deploy** — ton chat est en ligne en 2 minutes !

## Fonctionnalités

- Chat en temps réel avec WebSockets
- 4 salons : General, Idées, Design, Gaming
- Couleur personnalisée par utilisateur
- Indicateur "X écrit..."
- Compteur de membres en ligne
- Liste des membres connectés
- Fonctionne sur mobile
