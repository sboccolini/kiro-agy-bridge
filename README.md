# Chats compartidos de Kiro-Cli y Antigravity-Cli

Este es un script en Python (`select_chat.py`) que funciona como un puente e interfaz unificada (TUI) para gestionar conversaciones entre dos asistentes de IA de terminal:
* **Kiro** (`kiro-cli`)
* **Agy** (`antigravity-cli`)

## ¿Qué problema resuelve?
Cada asistente guarda su propio historial y contexto. Si empezás una sesión en Kiro, normalmente no podrías seguirla en Agy (y viceversa). Este script soluciona ese problema:
* **Lista unificada**: Muestra un listado centralizado (vía `curses`) de todas las conversaciones pasadas de ambos agentes.
* **Inyección de contexto**: Permite seleccionar un chat iniciado originalmente en Kiro y "pasarlo" a Agy, inyectando todo el historial previo como contexto interactivo, respetando los límites de memoria de argumentos del sistema operativo (~80KB).
* **Sincronización inversa**: Permite tomar una sesión avanzada en Agy e importar los mensajes nuevos hacia el archivo de historial de Kiro, haciendo una fusión (merge) inteligente y no destructiva para continuar el trabajo.

## Características
* Interfaz TUI moderna por consola con soporte para navegar mediante flechas (`↑` / `↓` o `j` / `k`).
* Panel de vista previa del historial (split-pane) en tiempo real para ver los últimos mensajes de cada chat antes de abrirlo.
* Búsqueda en vivo (presionando `/`).
* Identificación visual del agente original (`[KIRO]`, `[AGY]`, `[KIRO+AGY]`).
* Solución a límites del Kernel (`[Errno 7] Argument list too long`) mediante truncamiento inteligente priorizando los mensajes más recientes.
* Captura de errores de ejecución (`/tmp/select_chat_error.log`).

## Uso
El script está pensado para ejecutarse standalone (generalmente guardado en `~/.local/bin/select_chat.py`). 

Al ejecutar el script, te presentará la TUI:
* Navegá entre sesiones.
* Presioná `Enter` para ver los detalles.
* Presioná `a` para abrir directamente la sesión con Agy.
* Presioná `k` para abrir directamente la sesión con Kiro.

## Requisitos
* Python 3.x
* Módulo `curses` (incluido en la biblioteca estándar de Linux/macOS)
* Acceso a `kiro-cli` y `agy` instalados en el sistema (por ejemplo, en `~/.local/bin/`).

## Instalación
Copiá `select_chat.py` a alguna ruta dentro de tu `$PATH`, por ejemplo:
```bash
cp select_chat.py ~/.local/bin/select_chat
chmod +x ~/.local/bin/select_chat
```
Luego simplemente podés ejecutar:
```bash
select_chat
```
