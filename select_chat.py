#!/usr/bin/env python3
"""
select_chat.py — Selector de chats compatible con Kiro y Agy (antigravity-cli)
Permite continuar cualquier conversación en cualquiera de los dos agentes.
"""
import os
import re
import sys
import json
import glob

try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                                QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, 
                                QAbstractItemView, QTextEdit, QPushButton, QLabel, QHeaderView)
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QColor, QFont
except ImportError:
    import sys
    print("\n[!] Error: PyQt6 no está instalado.")
    print("    Por favor instalalo usando: pip install PyQt6")
    sys.exit(1)
import shutil
import uuid
import sqlite3
from datetime import datetime, timedelta

import curses
import textwrap

# ANSI Colors (usados fuera de curses, ej. mensajes de sync antes de exec)
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_CYAN    = "\033[96m"
C_GREEN   = "\033[92m"
C_YELLOW  = "\033[93m"
C_RED     = "\033[91m"
C_BLUE    = "\033[94m"
C_MAGENTA = "\033[95m"
C_GRAY    = "\033[90m"
C_WHITE   = "\033[97m"

# ─── Rutas base ──────────────────────────────────────────────────────────────
KIRO_SESSIONS_DIR = os.path.expanduser("~/.kiro/sessions/cli")
AGY_BASE_DIR      = os.path.expanduser("~/.gemini/antigravity-cli")
AGY_HISTORY       = os.path.join(AGY_BASE_DIR, "history.jsonl")
AGY_CONVS_DIR     = os.path.join(AGY_BASE_DIR, "conversations")
AGY_BRAIN_DIR     = os.path.join(AGY_BASE_DIR, "brain")

# ─── Utilidades generales ─────────────────────────────────────────────────────

def clear_screen():
    os.system("clear")

def parse_iso_to_timestamp(iso_str):
    if not iso_str:
        return 0
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        if "." in iso_str:
            base, rest = iso_str.split(".", 1)
            ns = rest.split("+")[0][:6]
            tz_part = rest[len(ns):]
            iso_str = f"{base}.{ns}{tz_part}"
        return datetime.fromisoformat(iso_str).timestamp()
    except Exception:
        try:
            clean = iso_str.split(".")[0].replace("Z", "")
            return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").timestamp()
        except Exception:
            return 0

def format_time(ts):
    if not ts:
        return "Fecha desconocida"
    dt   = datetime.fromtimestamp(ts)
    now  = datetime.now()
    if dt.date() == now.date():
        return f"Hoy {dt.strftime('%H:%M:%S')}"
    elif dt.date() == (now - timedelta(days=1)).date():
        return f"Ayer {dt.strftime('%H:%M:%S')}"
    return dt.strftime("%Y-%m-%d %H:%M")

# ─── Comprobación de presencia en sistemas ────────────────────────────────────

def agy_has_db(conv_id: str) -> bool:
    """Devuelve True si Agy tiene el archivo .db para esa conversación."""
    return os.path.isfile(os.path.join(AGY_CONVS_DIR, f"{conv_id}.db"))

def agy_has_real_session(conv_id: str) -> bool:
    """
    Devuelve True si Agy tiene una sesión REAL del servidor para esa conversación.
    Un DB con 0 steps fue creado por nosotros como stub y NO tiene trayectoria server-side.
    Un DB con steps reales sí puede reanudarse con --conversation.
    """
    db_path = os.path.join(AGY_CONVS_DIR, f"{conv_id}.db")
    if not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM steps")
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False

def agy_has_history_entry(conv_id: str) -> bool:
    """Devuelve True si conv_id aparece en history.jsonl de Agy."""
    if not os.path.isfile(AGY_HISTORY):
        return False
    with open(AGY_HISTORY, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("conversationId") == conv_id:
                    return True
            except Exception:
                pass
    return False

def kiro_has_session(conv_id: str) -> bool:
    """Devuelve True si Kiro tiene la sesión con ese ID."""
    return os.path.isfile(os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.json"))

# ─── Carga de sesiones ────────────────────────────────────────────────────────

def get_kiro_sessions():
    sessions = []
    for path in glob.glob(os.path.join(KIRO_SESSIONS_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            session_id = data.get("session_id")
            if not session_id:
                continue
            
            # Filtrar sesiones que no tienen mensajes (.jsonl no existe o está vacío)
            jsonl_path = os.path.join(KIRO_SESSIONS_DIR, f"{session_id}.jsonl")
            if not os.path.isfile(jsonl_path) or os.path.getsize(jsonl_path) == 0:
                continue

            sessions.append({
                "id":             session_id,
                "cwd":            data.get("cwd", ""),
                "title":          data.get("title") or "Sin título",
                "timestamp":      parse_iso_to_timestamp(data.get("updated_at", "")),
                "in_kiro":        True,
                "in_agy":         agy_has_real_session(session_id),
            })
        except Exception:
            pass
    return sessions

def get_agy_sessions():
    sessions = {}
    if not os.path.isfile(AGY_HISTORY):
        return []
    with open(AGY_HISTORY, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data    = json.loads(line)
                conv_id = data.get("conversationId")
                if not conv_id:
                    continue
                ts      = data.get("timestamp", 0) / 1000.0
                ws      = data.get("workspace", "")
                display = data.get("display", "")

                if conv_id not in sessions:
                    sessions[conv_id] = {
                        "id":        conv_id,
                        "cwd":       ws,
                        "display":   display,
                        "timestamp": ts,
                        "in_agy":    agy_has_real_session(conv_id),
                        "in_kiro":   kiro_has_session(conv_id),
                    }
                else:
                    if ts > sessions[conv_id]["timestamp"]:
                        sessions[conv_id]["timestamp"] = ts
                    if ws:
                        sessions[conv_id]["cwd"] = ws
            except Exception:
                pass

    result = list(sessions.values())
    for s in result:
        first = s.get("display", "Sin título").strip().replace("\n", " ")
        s["title"] = (first[:60] + "...") if len(first) > 60 else (first or "Sin título")
    return result

def merge_sessions(kiro_list, agy_list):
    """
    Combina ambas listas usando el ID como clave.
    in_agy se propaga desde el valor real de cada sesión (True solo si tiene steps reales).
    """
    merged = {}

    for s in kiro_list:
        merged[s["id"]] = s.copy()
        merged[s["id"]]["original_agent"] = "kiro"

    for s in agy_list:
        sid = s["id"]
        if sid in merged:
            existing = merged[sid]
            if s["timestamp"] > existing["timestamp"]:
                existing["timestamp"] = s["timestamp"]
            # Propagar in_agy desde el valor real (no hardcodear True)
            existing["in_agy"]  = s.get("in_agy", False)
            existing["in_kiro"] = True
            if s.get("title") and s["title"] != "Sin título" and existing.get("title") == "Sin título":
                existing["title"] = s["title"]
        else:
            entry = s.copy()
            entry["original_agent"] = "agy"
            merged[sid] = entry

    return sorted(merged.values(), key=lambda x: x["timestamp"], reverse=True)

# ─── Búsqueda profunda y Renombrar ─────────────────────────────────────────────

def search_in_content(sessions, query):
    q = query.lower()
    matched = []
    for s in sessions:
        found = False
        sid = s["id"]
        
        # Check Kiro JSONL
        kiro_path = os.path.join(KIRO_SESSIONS_DIR, f"{sid}.jsonl")
        if os.path.isfile(kiro_path):
            try:
                with open(kiro_path, "r", encoding="utf-8") as f:
                    if q in f.read().lower():
                        matched.append(s)
                        found = True
            except Exception:
                pass
                
        # Check Agy Transcript
        if not found:
            agy_path = os.path.join(AGY_BRAIN_DIR, sid, ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(agy_path):
                try:
                    with open(agy_path, "r", encoding="utf-8") as f:
                        if q in f.read().lower():
                            matched.append(s)
                except Exception:
                    pass
    return matched

def rename_session(session, new_title):
    import time
    sid = session["id"]
    success = False
    
    # Rename in Kiro
    kiro_json = os.path.join(KIRO_SESSIONS_DIR, f"{sid}.json")
    if os.path.isfile(kiro_json):
        try:
            with open(kiro_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["title"] = new_title
            with open(kiro_json, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            success = True
        except Exception:
            pass

    # Rename in Agy (append to history.jsonl)
    if os.path.isfile(AGY_HISTORY):
        try:
            ws = session.get("cwd", "")
            entry = {
                "conversationId": sid,
                "timestamp": int(time.time() * 1000),
                "display": new_title,
                "workspace": ws
            }
            with open(AGY_HISTORY, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            success = True
        except Exception:
            pass
            
    return success

# ─── Vista previa del chat ────────────────────────────────────────────────────

def get_chat_preview(session):
    """Extrae los últimos mensajes de la conversación (preferencia: Kiro si está disponible)."""
    preview = []

    # Intentar leer desde Kiro JSONL
    if session.get("in_kiro"):
        sid  = session["id"]
        path = os.path.join(KIRO_SESSIONS_DIR, f"{sid}.jsonl")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        d    = json.loads(line)
                        kind = d.get("kind")
                        if kind == "Prompt":
                            parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                            text  = "".join(parts).strip()
                            if text:
                                preview.append({"role": "user", "text": text})
                        elif kind == "AssistantMessage":
                            parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                            text  = "".join(parts).strip()
                            if text and "Tool uses were interrupted" not in text:
                                preview.append({"role": "assistant", "text": text})
                    except Exception:
                        pass
            if preview:
                return preview

    # Fallback: leer transcript de Agy
    if session.get("in_agy"):
        sid  = session["id"]
        path = os.path.join(AGY_BRAIN_DIR, sid, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        t = d.get("type")
                        if t == "USER_INPUT":
                            content = d.get("content", "")
                            if "<USER_REQUEST>" in content:
                                try:
                                    content = content.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0]
                                except Exception:
                                    pass
                            content = content.strip()
                            if content:
                                preview.append({"role": "user", "text": content})
                        elif t == "PLANNER_RESPONSE":
                            if not d.get("tool_calls"):
                                content = d.get("content", "").strip()
                                if content and "Tool uses were interrupted" not in content:
                                    preview.append({"role": "assistant", "text": content})
                    except Exception:
                        pass

    return preview

# ─── Sincronización Agy → Kiro ───────────────────────────────────────────────

def sync_agy_to_kiro(session):
    """
    Exporta/fusiona la conversación de Agy al formato JSONL/JSON de Kiro.
    Si ya existe un JSONL de Kiro, agrega los mensajes de Agy que no estén ya incluidos
    (merge por contenido) para preservar el historial completo de ambos sistemas.
    """
    conv_id   = session["id"]
    cwd       = session["cwd"]
    title     = session["title"]
    timestamp = session["timestamp"]

    transcript_path = os.path.join(AGY_BRAIN_DIR, conv_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.isfile(transcript_path):
        return False, "No se encontró el transcript de Agy."

    kiro_json_path  = os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.json")
    kiro_jsonl_path = os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.jsonl")

    # Leer mensajes existentes de Kiro (si los hay) para no duplicar
    existing_texts = set()
    existing_lines = []
    if os.path.isfile(kiro_jsonl_path):
        with open(kiro_jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                    text  = "".join(parts).strip()
                    if text:
                        existing_texts.add(text[:200])  # clave de dedup por primeros 200 chars
                    existing_lines.append(line.strip())
                except Exception:
                    pass

    # Convertir mensajes de Agy → formato Kiro (solo los que no estén ya en Kiro)
    new_kiro_lines = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d              = json.loads(line)
                t              = d.get("type")
                created_at_str = d.get("created_at", "")
                ts             = int(parse_iso_to_timestamp(created_at_str))

                if t == "USER_INPUT":
                    content = d.get("content", "")
                    if "<USER_REQUEST>" in content:
                        try:
                            content = content.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0]
                        except Exception:
                            pass
                    content = content.strip()
                    if content and content[:200] not in existing_texts:
                        new_kiro_lines.append({
                            "version": "v1",
                            "kind":    "Prompt",
                            "data": {
                                "message_id": str(uuid.uuid4()),
                                "content":    [{"kind": "text", "data": content}],
                                "meta":       {"timestamp": ts}
                            }
                        })
                        existing_texts.add(content[:200])

                elif t == "PLANNER_RESPONSE":
                    if not d.get("tool_calls"):
                        content = d.get("content", "").strip()
                        if content and "Tool uses were interrupted" not in content and content[:200] not in existing_texts:
                            new_kiro_lines.append({
                                "version": "v1",
                                "kind":    "AssistantMessage",
                                "data": {
                                    "message_id": str(uuid.uuid4()),
                                    "content":    [{"kind": "text", "data": content}],
                                    "meta":       {"timestamp": ts}
                                }
                            })
                            existing_texts.add(content[:200])
            except Exception:
                pass

    os.makedirs(KIRO_SESSIONS_DIR, exist_ok=True)

    # Escribir el JSONL combinado: historial de Kiro + mensajes nuevos de Agy
    with open(kiro_jsonl_path, "w", encoding="utf-8") as f:
        for line in existing_lines:
            f.write(line + "\n")
        for entry in new_kiro_lines:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    iso_time = datetime.fromtimestamp(timestamp).isoformat() + "Z"
    meta = {
        "session_id":    conv_id,
        "cwd":           cwd,
        "created_at":    iso_time,
        "updated_at":    iso_time,
        "title":         title,
        "session_state": {
            "version": "v1",
            "conversation_metadata": {
                "user_turn_metadatas":     [],
                "user_turn_start_request": None,
                "last_request":            None
            },
            "rts_model_state": {
                "conversation_id":          conv_id,
                "model_info":               None,
                "context_usage_percentage": None
            },
            "permissions": {
                "filesystem": {
                    "allowed_read_paths":  [cwd],
                    "allowed_write_paths": [],
                    "denied_read_paths":   [],
                    "denied_write_paths":  []
                },
                "trusted_tools":    [],
                "denied_tools":     [],
                "allowed_commands": []
            },
            "agent_name": None
        }
    }
    with open(kiro_json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    total = len(existing_lines) + len(new_kiro_lines)
    return True, f"Historial sincronizado: {len(existing_lines)} de Kiro + {len(new_kiro_lines)} nuevos de Agy = {total} mensajes."


# ─── Sincronización Kiro → Agy ───────────────────────────────────────────────

# ─── Creación de DB de Agy para sesiones de Kiro ────────────────────────────

AGY_DB_SCHEMA = '''
CREATE TABLE IF NOT EXISTS "battle_mode_infos" ("idx" integer,"data" blob,PRIMARY KEY ("idx"));
CREATE TABLE IF NOT EXISTS "executor_metadata" ("idx" integer,"data" blob,PRIMARY KEY ("idx"));
CREATE TABLE IF NOT EXISTS "gen_metadata" ("idx" integer,"data" blob,"size" integer NOT NULL DEFAULT 0,PRIMARY KEY ("idx"));
CREATE TABLE IF NOT EXISTS "parent_references" ("idx" integer,"data" blob,PRIMARY KEY ("idx"));
CREATE TABLE IF NOT EXISTS "steps" ("idx" integer,"step_type" integer NOT NULL DEFAULT 0,"status" integer NOT NULL DEFAULT 0,"has_subtrajectory" numeric NOT NULL DEFAULT false,"metadata" blob,"error_details" blob,"permissions" blob,"task_details" blob,"render_info" blob,"step_payload" blob,"step_format" integer NOT NULL DEFAULT 0,PRIMARY KEY ("idx"));
CREATE TABLE IF NOT EXISTS "trajectory_meta" ("trajectory_id" text,"cascade_id" text,"trajectory_type" integer,"source" integer,PRIMARY KEY ("trajectory_id"));
CREATE TABLE IF NOT EXISTS "trajectory_metadata_blob" ("id" text DEFAULT "main","data" blob,PRIMARY KEY ("id"));
'''

def _find_donor_blob(target_cwd: str):
    """
    Busca un DB existente cuyo blob de metadata corresponda al mismo workspace
    que target_cwd. Devuelve el blob bytes o None si no encuentra ninguno.
    Lo usamos como template para el nuevo DB, ya que el blob es protobuf binario
    y codifica permisos/workspace de forma que Agy reconozca la sesión.
    """
    target_uri = "file://" + target_cwd.replace(" ", "%20")
    best_blob  = None
    fallback   = None

    for db_path in glob.glob(os.path.join(AGY_CONVS_DIR, "*.db")):
        try:
            conn = sqlite3.connect(db_path)
            cur  = conn.cursor()
            cur.execute("SELECT data FROM trajectory_metadata_blob")
            row = cur.fetchone()
            conn.close()
            if not row:
                continue
            blob = bytes(row[0])
            # Buscar la URI del workspace en el blob
            try:
                text = blob.decode("utf-8", errors="replace")
                if target_uri in text:
                    best_blob = blob
                    break
                # Guardar como fallback si tiene el path sin codificar
                if target_cwd in text and fallback is None:
                    fallback = blob
            except Exception:
                pass
            if fallback is None:
                fallback = blob  # cualquier blob sirve como último recurso
        except Exception:
            pass

    return best_blob or fallback

def _build_workspace_blob(cwd: str) -> bytes:
    """
    Construye un blob protobuf mínimo que codifica el workspace.
    Basado en ingeniería inversa del formato real de Agy.
    Field 1 = sub-proto con workspace URI + sub-field 1a (vacío)
    Field 7 = workspace URI plain
    """
    uri = ("file://" + cwd.replace(" ", "%20")).encode("utf-8")

    def encode_varint(n):
        parts = []
        while n > 0x7F:
            parts.append((n & 0x7F) | 0x80)
            n >>= 7
        parts.append(n)
        return bytes(parts)

    def encode_field(field_num, wire_type, data: bytes) -> bytes:
        tag = (field_num << 3) | wire_type
        return encode_varint(tag) + encode_varint(len(data)) + data

    # Field 1 contains sub-proto: field 1 = URI, field 3 = empty
    inner = encode_field(1, 2, uri) + encode_field(3, 2, b"")
    blob  = encode_field(1, 2, inner)
    # Field 7 = plain workspace URI
    blob += encode_field(7, 2, uri)
    return blob


def _create_agy_db(conv_id: str, cwd: str) -> bool:
    """
    Crea el archivo .db de SQLite que Agy necesita para reconocer una conversación.
    Intenta reutilizar el blob de metadata de un DB existente del mismo workspace.
    Si no lo encuentra, construye un blob mínimo.
    Devuelve True si tuvo éxito.
    """
    db_path = os.path.join(AGY_CONVS_DIR, f"{conv_id}.db")
    if os.path.isfile(db_path):
        return True  # Ya existe

    os.makedirs(AGY_CONVS_DIR, exist_ok=True)

    # Obtener blob de metadata (preferir del mismo workspace)
    blob = _find_donor_blob(cwd)
    if blob is None:
        blob = _build_workspace_blob(cwd)

    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(AGY_DB_SCHEMA)
        traj_id = str(uuid.uuid4())
        conn.execute(
            "INSERT OR IGNORE INTO trajectory_meta (trajectory_id, cascade_id, trajectory_type, source) VALUES (?, ?, ?, ?)",
            (traj_id, conv_id, 4, 17)
        )
        conn.execute(
            "INSERT OR IGNORE INTO trajectory_metadata_blob (id, data) VALUES (?, ?)",
            ("main", blob)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        try:
            os.remove(db_path)
        except Exception:
            pass
        return False


def sync_kiro_to_agy(session):
    """
    Prepara una sesión de Kiro para abrirla con Agy:
    - Crea el .db stub (necesario solo para evitar el warning 'not found' si se usa --conversation)
    - Copia el transcript de Kiro al brain/ de Agy (para vista previa en el selector)
    NO agrega a history.jsonl porque eso causa que se identifique falsamente como sesión real.
    """
    conv_id = session["id"]
    cwd     = session["cwd"]
    title   = session["title"]

    # Crear brain dir y transcript.jsonl con el contenido de Kiro (para preview)
    brain_log_dir = os.path.join(AGY_BRAIN_DIR, conv_id, ".system_generated", "logs")
    os.makedirs(brain_log_dir, exist_ok=True)

    transcript_path = os.path.join(brain_log_dir, "transcript.jsonl")
    kiro_jsonl_path = os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.jsonl")

    if not os.path.isfile(transcript_path) and os.path.isfile(kiro_jsonl_path):
        agy_lines = []
        step_idx  = 0
        with open(kiro_jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d    = json.loads(line)
                    kind = d.get("kind")
                    ts   = d.get("data", {}).get("meta", {}).get("timestamp", 0)
                    dt   = datetime.fromtimestamp(ts).isoformat() if ts else datetime.now().isoformat()

                    if kind == "Prompt":
                        parts   = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                        content = "".join(parts).strip()
                        if content:
                            agy_lines.append({
                                "step_index": step_idx,
                                "source":     "USER_EXPLICIT",
                                "type":       "USER_INPUT",
                                "status":     "DONE",
                                "content":    content,
                                "created_at": dt,
                            })
                            step_idx += 1
                    elif kind == "AssistantMessage":
                        parts   = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                        content = "".join(parts).strip()
                        if content and "Tool uses were interrupted" not in content:
                            agy_lines.append({
                                "step_index": step_idx,
                                "source":     "MODEL",
                                "type":       "PLANNER_RESPONSE",
                                "status":     "DONE",
                                "content":    content,
                                "tool_calls": [],
                                "created_at": dt,
                            })
                            step_idx += 1
                except Exception:
                    pass

        with open(transcript_path, "w", encoding="utf-8") as f:
            for entry in agy_lines:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        full_path = os.path.join(brain_log_dir, "transcript_full.jsonl")
        if not os.path.isfile(full_path):
            shutil.copy2(transcript_path, full_path)

    n_msgs = len(open(kiro_jsonl_path).readlines()) if os.path.isfile(kiro_jsonl_path) else 0
    return True, f"Historial de Kiro preparado ({n_msgs} entradas)."

# ─── Lanzar agente ────────────────────────────────────────────────────────────


MAX_MSG_CHARS = 4000  # límite por mensaje (aumentado para contexto completo)

def build_kiro_context(session) -> str:
    """
    Construye un texto resumen del chat para inyectar como contexto en Agy.
    Lee de AMBAS fuentes (Kiro JSONL y Agy transcript) y combina los mensajes.
    """
    conv_id = session["id"]
    msgs = []
    sources_used = []

    # 1. Leer mensajes de Kiro JSONL
    kiro_jsonl = os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.jsonl")
    if os.path.isfile(kiro_jsonl):
        with open(kiro_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d    = json.loads(line)
                    kind = d.get("kind")
                    if kind == "Prompt":
                        parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                        text  = "".join(parts).strip()
                        if text:
                            msgs.append(("Usuario", text, "kiro"))
                    elif kind == "AssistantMessage":
                        parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                        text  = "".join(parts).strip()
                        if text and "Tool uses were interrupted" not in text:
                            msgs.append(("Asistente", text, "kiro"))
                except Exception:
                    pass
        if msgs:
            sources_used.append(f"Kiro ({len(msgs)} msgs)")

    # 2. Leer mensajes de Agy transcript (agregar los que no vengan de Kiro)
    agy_transcript = os.path.join(AGY_BRAIN_DIR, conv_id, ".system_generated", "logs", "transcript.jsonl")
    if os.path.isfile(agy_transcript):
        agy_msgs = []
        with open(agy_transcript, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    t = d.get("type")
                    if t == "USER_INPUT":
                        content = d.get("content", "")
                        if "<USER_REQUEST>" in content:
                            try:
                                content = content.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0]
                            except Exception:
                                pass
                        content = content.strip()
                        if content and "[CONTEXTO" not in content[:20]:
                            agy_msgs.append(("Usuario", content, "agy"))
                    elif t == "PLANNER_RESPONSE":
                        if not d.get("tool_calls"):
                            content = d.get("content", "").strip()
                            if content and "Tool uses were interrupted" not in content:
                                agy_msgs.append(("Asistente", content, "agy"))
                except Exception:
                    pass
        if agy_msgs:
            sources_used.append(f"Agy ({len(agy_msgs)} msgs)")
            # Si ya tenemos msgs de Kiro, agregar los de Agy al final (son posteriores)
            # Si no hay de Kiro, usar solo los de Agy
            if msgs:
                msgs.append(("--- CONTINUACIÓN EN AGY ---", "", "separator"))
            msgs.extend(agy_msgs)

    if not msgs:
        return ""

    total = len([m for m in msgs if m[2] != "separator"])
    src_str = " + ".join(sources_used) if sources_used else "desconocido"
    header = f"[CONTEXTO: Historial completo de la conversación. Fuentes: {src_str}. Total: {total} mensajes. Continuá desde aquí.]\n\n"
    footer = "[FIN DEL CONTEXTO — Seguí la conversación desde aquí. No expliques este mensaje, simplemente continuá.]"

    blocks = []

    for role, text, source in msgs:
        if source == "separator":
            blocks.append(f"\n---\n{role}\n---\n\n")
            continue
        if len(text) > MAX_MSG_CHARS:
            text = text[:MAX_MSG_CHARS] + f"\n... [truncado, {len(text) - MAX_MSG_CHARS} caracteres más]"
        
        block = f"### {role}:\n{text}\n\n"
        blocks.append(block)

    return header + "".join(blocks) + footer

def launch_agy(conv_id, cwd, new_session=False, context_prompt="", has_real_agy=False):
    """Lanza agy en el directorio dado.
    - new_session: abre sin contexto
    - context_prompt + has_real_agy=True:  --conversation <id> + --prompt-interactive (hay trajectory real)
    - context_prompt + has_real_agy=False: --prompt-interactive solo (sin trajectory server-side)
    - sin context_prompt: --conversation <id> (sesión Agy pura, sin historial de Kiro)
    """
    try:
        os.chdir(cwd)
    except Exception as e:
        print(f"{C_RED}No se pudo cambiar al directorio {cwd}: {e}{C_RESET}")
        input("Presiona Enter para volver...")
        return False

    clear_screen()
    if new_session:
        print(f"{C_GREEN}Iniciando nueva sesión con Agy en {cwd}...{C_RESET}\n")
        args = ["agy"]
    elif context_prompt:
        # Escribir contexto a archivo temporal para referencia
        import tempfile
        ctx_file = os.path.join(tempfile.gettempdir(), f"kiro_ctx_{conv_id[:8]}.md")
        with open(ctx_file, "w", encoding="utf-8") as f:
            f.write(context_prompt)
        
        if has_real_agy:
            # Ya tiene historial real en Agy → reanudar sin inyectar prompt
            # (combinar --conversation + --prompt-interactive causa errores del servidor)
            print(f"{C_GREEN}Reanudando {conv_id[:8]}... con Agy...{C_RESET}")
            print(f"{C_GRAY}  Contexto de Kiro guardado en: {ctx_file}{C_RESET}\n")
            args = ["agy", "--conversation", conv_id]
        else:
            # Solo en Kiro → abrir nueva sesión con prompt corto que apunta al archivo
            short_prompt = (
                f"[CONTEXTO IMPORTANTE] Leé el archivo {ctx_file} que contiene el historial "
                f"completo de una conversación previa de Kiro. Usá view_file para leerlo. "
                f"Una vez que lo hayas leído, respondé brevemente confirmando que tenés el contexto "
                f"y esperá las instrucciones del usuario. No expliques este mensaje, simplemente "
                f"continuá la conversación."
            )
            print(f"{C_GREEN}Abriendo en Agy con historial completo de Kiro...{C_RESET}\n")
            args = ["agy", "--prompt-interactive", short_prompt]
    else:
        print(f"{C_GREEN}Reanudando conversación {conv_id} con Agy en {cwd}...{C_RESET}\n")
        args = ["agy", "--conversation", conv_id]

    _exec_agent("agy", args)
    return True


def launch_kiro(conv_id, cwd, new_session=False):
    """Lanza kiro-cli en el directorio dado."""
    try:
        os.chdir(cwd)
    except Exception as e:
        print(f"{C_RED}No se pudo cambiar al directorio {cwd}: {e}{C_RESET}")
        input("Presiona Enter para volver...")
        return False

    clear_screen()
    if new_session:
        print(f"{C_GREEN}Iniciando nueva sesión con Kiro en {cwd}...{C_RESET}\n")
        args = ["kiro-cli", "chat"]
    else:
        print(f"{C_GREEN}Reanudando conversación {conv_id} con Kiro en {cwd}...{C_RESET}\n")
        args = ["kiro-cli", "chat", "--resume-id", conv_id]

    _exec_agent("kiro-cli", args)
    return True

def _restore_terminal():
    """Restaura el estado del terminal antes de hacer exec, limpiando posibles modos raw/noecho de curses."""
    try:
        import curses
        curses.endwin()
    except Exception:
        pass
    import os as _os
    _os.system("stty sane 2>/dev/null")

def _exec_agent(name, args):
    """Reemplaza el proceso actual con el agente. Si no hay terminal (GUI puro), abre una nueva."""
    import sys, subprocess, shlex
    try:
        if not sys.stdout.isatty():
            # Ejecutado sin terminal de fondo (ej. desde acceso directo)
            cmd_str = " ".join([shlex.quote(a) for a in args])
            subprocess.Popen(["gnome-terminal", "--", "bash", "-c", f"{cmd_str}; exec bash"])
            sys.exit(0)
            
        _restore_terminal()
        os.execlp(name, *args)
    except FileNotFoundError:
        # Intentar desde ~/.local/bin/
        local_path = os.path.expanduser(f"~/.local/bin/{name}")
        try:
            if not sys.stdout.isatty():
                args[0] = local_path
                cmd_str = " ".join([shlex.quote(a) for a in args])
                subprocess.Popen(["gnome-terminal", "--", "bash", "-c", f"{cmd_str}; exec bash"])
                sys.exit(0)
                
            _restore_terminal()
            os.execl(local_path, *args)
        except Exception as e:
            print(f"{C_RED}\n❌ No se encontró '{name}': {e}{C_RESET}")
            input("Presiona Enter para continuar...")
    except OSError as e:
        # Captura "Argument list too long" y otros errores del SO
        print(f"{C_RED}\n❌ Error del sistema al ejecutar '{name}': {e}{C_RESET}")
        input("Presiona Enter para continuar...")
    except Exception as e:
        print(f"{C_RED}\n❌ Error inesperado al ejecutar '{name}': {e}{C_RESET}")
        input("Presiona Enter para continuar...")

# ─── Verificar directorio de trabajo ─────────────────────────────────────────

def ensure_cwd(session):
    """Devuelve el cwd a usar o None si el usuario cancela."""
    cwd = session.get("cwd", "")
    if cwd and os.path.isdir(cwd):
        return cwd
    print(f"\n{C_YELLOW}⚠️  El directorio original '{cwd}' no existe.{C_RESET}")
    ans = input("¿Abrir en tu directorio personal (~) en su lugar? (S/n): ").strip().lower()
    if ans in ("", "s", "si", "y", "yes"):
        return os.path.expanduser("~")
    print("Cancelado.")
    input("Presiona Enter para volver...")
    return None

# ─── Interfaz de detalle de conversación ─────────────────────────────────────

def show_detail(session):
    """
    Muestra el detalle de la conversación seleccionada y permite elegir
    con qué agente continuarla.
    Retorna 'back' | 'quit'
    """
    in_kiro = session.get("in_kiro", False)
    in_agy  = session.get("in_agy",  False)

    while True:
        clear_screen()
        print(f"{C_BOLD}{C_MAGENTA}╔══════════════════════════════════════════════════════════╗{C_RESET}")
        print(f"{C_BOLD}{C_CYAN}║                 DETALLE DE CONVERSACIÓN                  ║{C_RESET}")
        print(f"{C_BOLD}{C_MAGENTA}╚══════════════════════════════════════════════════════════╝{C_RESET}\n")

        # Info básica
        print(f"  {C_BOLD}ID:{C_RESET}         {C_GRAY}{session['id']}{C_RESET}")
        print(f"  {C_BOLD}Título:{C_RESET}     {session['title']}")
        print(f"  {C_BOLD}Fecha:{C_RESET}      {format_time(session['timestamp'])}")
        print(f"  {C_BOLD}Directorio:{C_RESET} {session['cwd']}")

        # Tags de presencia
        tags = []
        if in_kiro:
            tags.append(f"{C_YELLOW}[KIRO]{C_RESET}")
        if in_agy:
            tags.append(f"{C_GREEN}[AGY]{C_RESET}")
        print(f"  {C_BOLD}Sistemas:{C_RESET}   {' '.join(tags) if tags else C_GRAY + 'Desconocido' + C_RESET}\n")

        # Vista previa del chat
        preview = get_chat_preview(session)
        print(f"  {C_BOLD}{C_BLUE}─── Diálogo reciente ({min(len(preview), 5)} mensajes) ───{C_RESET}")
        if not preview:
            print(f"    {C_GRAY}(No hay mensajes registrados){C_RESET}")
        else:
            for msg in preview[-5:]:
                role_tag = f"{C_CYAN}👤 Usuario:{C_RESET}" if msg["role"] == "user" else f"{C_GREEN}🤖 Asistente:{C_RESET}"
                txt = msg["text"].strip().replace("\n", " ")
                if len(txt) > 70:
                    txt = txt[:70] + "..."
                print(f"    {role_tag} {txt}")
        print(f"  {C_BLUE}──────────────────────────────────────────────────────{C_RESET}\n")

        # Opciones disponibles
        print(f"  {C_BOLD}¿Qué deseas hacer?{C_RESET}")

        # Opción 1: Agy
        if in_agy:
            print(f"    {C_BOLD}[1]{C_RESET} Continuar con {C_GREEN}Agy (antigravity){C_RESET}  ✅ listo")
        else:
            print(f"    {C_BOLD}[1]{C_RESET} Continuar con {C_GREEN}Agy (antigravity){C_RESET}  {C_YELLOW}[requiere preparación]{C_RESET}")

        # Opción 2: Kiro
        if in_kiro:
            print(f"    {C_BOLD}[2]{C_RESET} Continuar con {C_YELLOW}Kiro (kiro-cli){C_RESET}   ✅ listo")
        else:
            print(f"    {C_BOLD}[2]{C_RESET} Continuar con {C_YELLOW}Kiro (kiro-cli){C_RESET}   {C_CYAN}[sincronización automática]{C_RESET}")

        print(f"    {C_BOLD}[3]{C_RESET} Volver al listado")
        print(f"    {C_BOLD}[4]{C_RESET} Salir")

        choice = input(f"\n{C_BOLD}Selección [1-4] > {C_RESET}").strip()

        # ── Opción 1: Continuar con AGY ──────────────────────────────────────
        if choice == "1":
            cwd = ensure_cwd(session)
            if not cwd:
                continue

            if in_kiro:
                # La sesión tiene historial en Kiro → siempre inyectar contexto
                print(f"\n{C_CYAN}📜 Preparando historial de Kiro para Agy...{C_RESET}")
                sync_kiro_to_agy(session)  # copia transcript a brain/ para preview
                ctx = build_kiro_context(session)
                if ctx:
                    launch_agy(session["id"], cwd,
                               context_prompt=ctx,
                               has_real_agy=in_agy)  # True → usa --conversation también
                else:
                    # Sin historial en JSONL (sesión vacía): abrir normalmente
                    launch_agy(session["id"], cwd)
            else:
                # Solo en Agy, sin historial de Kiro: reanudar directamente
                launch_agy(session["id"], cwd)

        # ── Opción 2: Continuar con KIRO ─────────────────────────────────────
        elif choice == "2":
            cwd = ensure_cwd(session)
            if not cwd:
                continue

            if in_agy:
                # La sesión tiene historial en Agy → sincronizar al JSONL de Kiro primero
                print(f"\n{C_CYAN}📜 Sincronizando historial de Agy → Kiro...{C_RESET}")
                ok, msg = sync_agy_to_kiro(session)
                if ok:
                    print(f"{C_GREEN}✅ {msg}{C_RESET}")
                    launch_kiro(session["id"], cwd)
                else:
                    print(f"{C_RED}❌ {msg}{C_RESET}")
                    input("Presiona Enter para continuar...")
            else:
                # Solo en Kiro (sin historial de Agy): reanudar directamente
                launch_kiro(session["id"], cwd)

        elif choice == "3":
            return "back"
        elif choice == "4":
            return "quit"
        else:
            print(f"{C_RED}Opción no válida.{C_RESET}")
            input("Presiona Enter para continuar...")

# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# GUI con PyQt6
# ─────────────────────────────────────────────────────────────────────────────

class ChatSelectorGUI(QMainWindow):
    def __init__(self, sessions):
        super().__init__()
        self.sessions = sessions
        self.filtered = sessions[:]
        self.selected_session = None
        self.selected_action = None
        
        self.setWindowTitle("Selector de Chats - Kiro & Antigravity")
        self.resize(1100, 700)
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e2e; }
            QLineEdit { background-color: #313244; color: white; border-radius: 15px; padding: 5px 15px; font-size: 14px; border: 1px solid #45475a; }
            QTableWidget { background-color: #181825; color: #cdd6f4; gridline-color: #313244; border: none; font-size: 13px; }
            QTableWidget::item:selected { background-color: #45475a; }
            QHeaderView::section { background-color: #11111b; color: #a6adc8; padding: 5px; border: none; font-weight: bold; }
            QTextEdit { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; border-radius: 5px; padding: 10px; font-size: 14px; }
            QPushButton { background-color: #313244; color: white; border-radius: 15px; padding: 8px 20px; font-weight: bold; border: 1px solid #45475a; }
            QPushButton:hover { background-color: #45475a; }
            QLabel { color: #a6adc8; }
        """)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        header_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Buscar palabra o contenido en el chat...")
        self.search_bar.setFixedWidth(400)
        self.search_bar.textChanged.connect(self.on_search_changed)
        
        title_label = QLabel("<b>Selector de Chats</b><br><span style='font-size:11px'>Kiro & Antigravity</span>")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        header_layout.addWidget(self.search_bar)
        header_layout.addStretch()
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        split_layout = QHBoxLayout()
        
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Fecha", "Origen", "Título / Primer Mensaje"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.doubleClicked.connect(self.on_double_click)
        split_layout.addWidget(self.table, stretch=2)
        
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        split_layout.addWidget(self.preview, stretch=3)
        
        layout.addLayout(split_layout)
        
        footer_layout = QHBoxLayout()
        self.status_label = QLabel(f"Cargadas {len(sessions)} conversaciones.")
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch()
        
        self.btn_rename = QPushButton("✏️ Renombrar")
        self.btn_rename.clicked.connect(self.rename_current)
        self.btn_rename.setStyleSheet("QPushButton { background-color: #313244; color: #cba6f7; border: 1px solid #cba6f7; } QPushButton:hover { background-color: #45475a; }")
        footer_layout.addWidget(self.btn_rename)

        self.btn_agy = QPushButton("Continuar en Agy")
        self.btn_kiro = QPushButton("Continuar en Kiro")
        self.btn_agy.clicked.connect(lambda: self.trigger_action(ord('a')))
        self.btn_kiro.clicked.connect(lambda: self.trigger_action(ord('k')))
        
        self.btn_agy.setStyleSheet("QPushButton { background-color: #2e4a3b; color: #a6e3a1; border: 1px solid #a6e3a1; } QPushButton:hover { background-color: #3b5a48; } QPushButton:disabled { background-color: #1e1e2e; color: #45475a; border-color: #45475a; }")
        self.btn_kiro.setStyleSheet("QPushButton { background-color: #4a452e; color: #f9e2af; border: 1px solid #f9e2af; } QPushButton:hover { background-color: #5a553e; } QPushButton:disabled { background-color: #1e1e2e; color: #45475a; border-color: #45475a; }")
        
        footer_layout.addWidget(self.btn_agy)
        footer_layout.addWidget(self.btn_kiro)
        layout.addLayout(footer_layout)
        
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)
        
        self.populate_table()

    def on_search_changed(self, text):
        self.search_timer.start(300)
        
    def perform_search(self):
        query = self.search_bar.text().lower()
        if not query:
            self.filtered = self.sessions[:]
        else:
            self.filtered = search_in_content(self.sessions, query)
        self.populate_table()
        self.status_label.setText(f"Resultados: {len(self.filtered)} de {len(self.sessions)} conversaciones.")

    def rename_current(self):
        if not self.selected_session: return
        from PyQt6.QtWidgets import QInputDialog
        new_title, ok = QInputDialog.getText(self, "Renombrar", "Nuevo título:", text=self.selected_session.get("title", ""))
        if ok and new_title.strip():
            new_title = new_title.strip()
            if rename_session(self.selected_session, new_title):
                self.selected_session["title"] = new_title
                self.populate_table()

    def populate_table(self):
        self.table.setRowCount(0)
        for row, s in enumerate(self.filtered):
            self.table.insertRow(row)
            
            from datetime import datetime
            time_str = datetime.fromtimestamp(s.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
            
            in_k = s.get("in_kiro")
            in_a = s.get("in_agy")
            if in_k and in_a:
                tag = "Kiro+Agy"
                color = QColor("#cba6f7") # Magenta
            elif in_k:
                tag = "Kiro"
                color = QColor("#f9e2af") # Yellow
            else:
                tag = "Agy"
                color = QColor("#a6e3a1") # Green
                
            title = s.get("title", "")
            
            item_date = QTableWidgetItem(time_str)
            item_tag = QTableWidgetItem(tag)
            item_tag.setForeground(color)
            item_title = QTableWidgetItem(title)
            
            item_date.setFlags(item_date.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_tag.setFlags(item_tag.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item_title.setFlags(item_title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            
            self.table.setItem(row, 0, item_date)
            self.table.setItem(row, 1, item_tag)
            self.table.setItem(row, 2, item_title)
            
        if self.filtered:
            self.table.selectRow(0)

    def on_selection_changed(self):
        selected = self.table.selectedItems()
        if not selected:
            self.btn_agy.setEnabled(False)
            self.btn_kiro.setEnabled(False)
            self.btn_rename.setEnabled(False)
            self.preview.setHtml("")
            return
            
        row = selected[0].row()
        session = self.filtered[row]
        self.selected_session = session
        
        self.btn_agy.setEnabled(True)
        self.btn_kiro.setEnabled(True)
        self.btn_rename.setEnabled(True)
        
        preview = get_chat_preview(session)
        html = f"<h3 style='color:#89b4fa; margin-top:0;'>{session.get('title')}</h3>"
        if not preview:
            html += "<p style='color:#a6adc8'><i>(Sin mensajes)</i></p>"
        else:
            for msg in preview[-15:]:
                role = "👤" if msg["role"] == "user" else "🤖"
                col = "#a6e3a1" if msg["role"] == "assistant" else "#89b4fa"
                text = msg["text"].replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                html += f"<p><b>{role}</b> <span style='color:{col}'>{text}</span></p><hr style='border: 0; height: 1px; background-color: #313244;'/>"
        
        self.preview.setHtml(html)

    def on_double_click(self):
        if not self.selected_session: return
        in_agy = self.selected_session.get("in_agy")
        self.trigger_action(ord('a') if in_agy else ord('k'))

    def trigger_action(self, key):
        if self.selected_session:
            self.selected_action = key
            self.close()

def main():
    kiro_list    = get_kiro_sessions()
    agy_list     = get_agy_sessions()
    all_sessions = merge_sessions(kiro_list, agy_list)

    if not all_sessions:
        print(f"{C_RED}No se encontraron conversaciones previas de Kiro ni de Agy.{C_RESET}")
        input("\nPresiona Enter para salir...")
        sys.exit(0)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    win = ChatSelectorGUI(all_sessions)
    win.show()
    app.exec()
    
    if win.selected_action and win.selected_session:
        result = (win.selected_session, win.selected_action)
    else:
        result = None

    if result is None:
        sys.exit(0)

    session, key = result
    in_kiro = session.get("in_kiro", False)
    in_agy  = session.get("in_agy",  False)

    if key == ord('a'):
        try:
            cwd = ensure_cwd(session)
            if cwd:
                if in_kiro:
                    os.system("clear")
                    print(f"\n{C_CYAN}📜 Preparando historial de Kiro para Agy...{C_RESET}")
                    sync_kiro_to_agy(session)
                    ctx = build_kiro_context(session)
                    if ctx:
                        launch_agy(session["id"], cwd, context_prompt=ctx, has_real_agy=in_agy)
                    else:
                        launch_agy(session["id"], cwd)
                else:
                    launch_agy(session["id"], cwd)
        except Exception as e:
            print(f"{C_RED}\n❌ Error abriendo con Agy: {e}{C_RESET}")
            input("  Presiona Enter para continuar...")

    elif key == ord('k'):
        try:
            cwd = ensure_cwd(session)
            if cwd:
                if in_agy:
                    os.system("clear")
                    print(f"\n{C_CYAN}📜 Sincronizando historial de Agy → Kiro...{C_RESET}")
                    ok, msg = sync_agy_to_kiro(session)
                    if ok:
                        print(f"{C_GREEN}✅ {msg}{C_RESET}")
                        launch_kiro(session["id"], cwd)
                    else:
                        print(f"{C_RED}❌ {msg}{C_RESET}")
                        input("Presiona Enter para continuar...")
                else:
                    launch_kiro(session["id"], cwd)
        except Exception as e:
            print(f"{C_RED}\n❌ Error abriendo con Kiro: {e}{C_RESET}")
            input("  Presiona Enter para continuar...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("  Presiona Enter para salir...")
        sys.exit(1)
