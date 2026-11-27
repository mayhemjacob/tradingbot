# tradingbot — contexto para Claude

## Guardar siempre en GitHub

Este proyecto está enlazado con https://github.com/mayhemjacob/tradingbot (rama `main`). Jacobo quiere que todo quede en GitHub para no depender de un solo ordenador:

- Al empezar una sesión: `git pull --ff-only` para traer lo último.
- Al terminar cada tarea o bloque de cambios coherente: `git add` de lo modificado, `git commit` con un mensaje claro en español y `git push origin main`, sin esperar a que te lo pida.
- Nunca subir `.env`, claves, contraseñas ni archivos pesados (audios, vídeos, zips): las claves van en 1Password y lo pesado en Google Drive (carpeta "Repo Big Files").
- Si el push falla (conflicto, credenciales), avisa a Jacobo en vez de forzarlo.
