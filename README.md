### A FastAPi, Jinja2 with tailwindcss starting template

Poetry config in poetry.toml has virtualenvs.in-project = true so 
vscode setup is easier. With this the virtualenv is created in the project
root and vscode should find it automatically.

## Docker/Podman
use Poetry Plugin:export to create requirements.txt
Install it using pipx
```sh
pipx inject poetry poetry-plugin-export
```
