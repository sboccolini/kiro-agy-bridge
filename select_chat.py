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
    Construye un texto resumen del chat de Kiro para inyectar como contexto
    en Agy via --prompt-interactive. Incluye los últimos N mensajes.
    """
    conv_id = session["id"]
    kiro_jsonl = os.path.join(KIRO_SESSIONS_DIR, f"{conv_id}.jsonl")
    if not os.path.isfile(kiro_jsonl):
        return ""

    msgs = []
    with open(kiro_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d    = json.loads(line)
                kind = d.get("kind")
                if kind == "Prompt":
                    parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                    text  = "".join(parts).strip()
                    if text:
                        msgs.append(("Usuario", text))
                elif kind == "AssistantMessage":
                    parts = [c.get("data", "") for c in d.get("data", {}).get("content", []) if c.get("kind") == "text"]
                    text  = "".join(parts).strip()
                    if text and "Tool uses were interrupted" not in text:
                        msgs.append(("Asistente", text))
            except Exception:
                pass

    if not msgs:
        return ""

    total = len(msgs)
    header = f"[CONTEXTO: Esta conversación fue iniciada en Kiro. Historial original: {total} mensajes. Continuá desde aquí.]\n\n"
    footer = "[FIN DEL CONTEXTO — Seguí la conversación desde aquí. No expliques este mensaje, simplemente continuá.]"

    safe_limit = 80_000
    blocks = []
    current_len = len(header.encode('utf-8')) + len(footer.encode('utf-8'))

    # Procesar desde los más recientes hacia atrás
    for role, text in reversed(msgs):
        if len(text) > MAX_MSG_CHARS:
            text = text[:MAX_MSG_CHARS] + f"\n... [truncado, {len(text) - MAX_MSG_CHARS} caracteres más]"
        
        block = f"### {role}:\n{text}\n\n"
        block_len = len(block.encode('utf-8'))
        
        if current_len + block_len > safe_limit:
            blocks.append(f"... [Se omitieron {total - len(blocks)} mensajes más antiguos por límite de sistema] ...\n\n")
            break
            
        blocks.append(block)
        current_len += block_len

    # Restaurar orden cronológico
    blocks.reverse()
    
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
    elif context_prompt and has_real_agy:
        print(f"{C_GREEN}Reanudando {conv_id[:8]}... con Agy (+ contexto de Kiro)...{C_RESET}\n")
        args = ["agy", "--conversation", conv_id, "--prompt-interactive", context_prompt]
    elif context_prompt:
        print(f"{C_GREEN}Abriendo en Agy con historial completo de Kiro...{C_RESET}\n")
        args = ["agy", "--prompt-interactive", context_prompt]
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
    """Reemplaza el proceso actual con el agente. Maneja errores de forma visible."""
    try:
        _restore_terminal()
        os.execlp(name, *args)
    except FileNotFoundError:
        # Intentar desde ~/.local/bin/
        local_path = os.path.expanduser(f"~/.local/bin/{name}")
        try:
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
# TUI con curses
# ─────────────────────────────────────────────────────────────────────────────

# Paleta de colores curses (índices de par)
_CLR_HEADER    = 1   # título principal
_CLR_SELECTED  = 2   # fila seleccionada
_CLR_KIRO      = 3   # badge Kiro
_CLR_AGY       = 4   # badge Agy
_CLR_BOTH      = 5   # badge Kiro+Agy
_CLR_DIM       = 6   # texto gris
_CLR_PREVIEW_U = 7   # mensaje usuario en preview
_CLR_PREVIEW_A = 8   # mensaje asistente en preview
_CLR_STATUS    = 9   # barra de estado
_CLR_SEARCH    = 10  # barra de búsqueda activa
_CLR_TITLE     = 11  # título de conversación
_CLR_ACTION    = 12  # botones de acción

def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    bg = -1
    curses.init_pair(_CLR_HEADER,    curses.COLOR_CYAN,    bg)
    curses.init_pair(_CLR_SELECTED,  curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(_CLR_KIRO,      curses.COLOR_YELLOW,  bg)
    curses.init_pair(_CLR_AGY,       curses.COLOR_GREEN,   bg)
    curses.init_pair(_CLR_BOTH,      curses.COLOR_MAGENTA, bg)
    curses.init_pair(_CLR_DIM,       8,                    bg)   # gray
    curses.init_pair(_CLR_PREVIEW_U, curses.COLOR_CYAN,    bg)
    curses.init_pair(_CLR_PREVIEW_A, curses.COLOR_GREEN,   bg)
    curses.init_pair(_CLR_STATUS,    curses.COLOR_BLACK,   curses.COLOR_BLUE)
    curses.init_pair(_CLR_SEARCH,    curses.COLOR_BLACK,   curses.COLOR_YELLOW)
    curses.init_pair(_CLR_TITLE,     curses.COLOR_WHITE,   bg)
    curses.init_pair(_CLR_ACTION,    curses.COLOR_BLACK,   curses.COLOR_GREEN)

def _safe_addstr(win, y, x, text, attr=0):
    """Escribe texto ignorando errores de desbordamiento de pantalla."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    available = w - x
    if available <= 0:
        return
    try:
        win.addstr(y, x, text[:available], attr)
    except curses.error:
        pass

def _draw_hline(win, y, x, length, char="─", attr=0):
    _safe_addstr(win, y, x, char * length, attr)

def _tag_str(session):
    in_k = session.get("in_kiro", False)
    in_a = session.get("in_agy",  False)
    if in_k and in_a:
        return (" ⬡ KIRO+AGY ", _CLR_BOTH)
    elif in_k:
        return (" ● KIRO ", _CLR_KIRO)
    else:
        return (" ● AGY  ", _CLR_AGY)

def _draw_list_panel(win, sessions, selected, scroll_off, search_query):
    """Renderiza el panel izquierdo: lista de conversaciones."""
    h, w = win.getmaxyx()
    win.erase()

    # Cabecera
    header = " 💬 CONVERSACIONES"
    _safe_addstr(win, 0, 0, header.ljust(w), curses.color_pair(_CLR_HEADER) | curses.A_BOLD)

    row = 1
    visible = h - 3  # filas disponibles para ítems
    for i in range(scroll_off, min(scroll_off + visible, len(sessions))):
        s = sessions[i]
        is_sel = (i == selected)

        tag_str, tag_clr = _tag_str(s)
        time_str = format_time(s["timestamp"])
        title = s["title"] or "Sin título"

        # Barra de selección de fondo
        base_attr = curses.color_pair(_CLR_SELECTED) if is_sel else 0
        _safe_addstr(win, row, 0, " " * (w - 1), base_attr)

        # Flecha de selección
        arrow = "▶ " if is_sel else "  "
        _safe_addstr(win, row, 0, arrow, base_attr | curses.A_BOLD)

        # Badge de agente
        tag_attr = (curses.color_pair(_CLR_SELECTED) if is_sel
                    else curses.color_pair(tag_clr) | curses.A_BOLD)
        _safe_addstr(win, row, 2, tag_str, tag_attr)

        # Título
        title_x   = 2 + len(tag_str)
        max_title = max(0, w - title_x - len(time_str) - 2)
        title_disp = title[:max_title]
        title_attr = base_attr | curses.A_BOLD
        _safe_addstr(win, row, title_x, title_disp, title_attr)

        # Fecha (alineada a la derecha)
        time_x = w - len(time_str) - 1
        _safe_addstr(win, row, time_x, time_str,
                     base_attr | curses.color_pair(_CLR_DIM))
        row += 1

    # Barra inferior: búsqueda / stats
    bar_y = h - 2
    if search_query:
        mode_str = "🔍 [BÚSQUEDA PROFUNDA]" if getattr(win, "is_deep_search", False) else "🔍"
        bar = f" {mode_str} {search_query}  [{len(sessions)} resultados]"
        _safe_addstr(win, bar_y, 0, bar.ljust(w - 1),
                     curses.color_pair(_CLR_SEARCH) | curses.A_BOLD)
    else:
        bar = f"  {len(sessions)} chats │ / buscar │ s buscar en texto │ ↑↓ navegar │ Enter elegir │ q salir"
        _safe_addstr(win, bar_y, 0, bar.ljust(w - 1),
                     curses.color_pair(_CLR_STATUS))

def _draw_preview_panel(win, session):
    """Renderiza el panel derecho: detalle y preview del chat seleccionado."""
    h, w = win.getmaxyx()
    win.erase()

    if session is None:
        _safe_addstr(win, h // 2, max(0, w // 2 - 8), "Sin selección",
                     curses.color_pair(_CLR_DIM))
        return

    row = 0

    # ── Cabecera con título ───────────────────────────────────────────────
    tag_str, tag_clr = _tag_str(session)
    hdr_attr = curses.color_pair(_CLR_HEADER) | curses.A_BOLD
    _safe_addstr(win, row, 0, " DETALLE ".center(w), hdr_attr)
    row += 1

    title = session.get("title", "Sin título")
    for line in textwrap.wrap(title, w - 2):
        _safe_addstr(win, row, 1, line, curses.A_BOLD | curses.color_pair(_CLR_TITLE))
        row += 1
    row += 1

    # ── Metadatos ─────────────────────────────────────────────────────────
    dim = curses.color_pair(_CLR_DIM)
    bold = curses.A_BOLD

    tag_attr = curses.color_pair(tag_clr) | curses.A_BOLD
    _safe_addstr(win, row, 1, "Sistema  ", dim)
    _safe_addstr(win, row, 10, tag_str.strip(), tag_attr)
    row += 1

    _safe_addstr(win, row, 1, "Fecha    ", dim)
    _safe_addstr(win, row, 10, format_time(session.get("timestamp", 0)), bold)
    row += 1

    cwd = session.get("cwd", "")
    _safe_addstr(win, row, 1, "Dir      ", dim)
    _safe_addstr(win, row, 10, cwd[:w - 11])
    row += 1

    short_id = session.get("id", "")[:20] + "…"
    _safe_addstr(win, row, 1, "ID       ", dim)
    _safe_addstr(win, row, 10, short_id, dim)
    row += 2

    # ── Separador ─────────────────────────────────────────────────────────
    _draw_hline(win, row, 0, w - 1, "─", dim)
    row += 1
    _safe_addstr(win, row, 1, "Diálogo reciente", dim | curses.A_BOLD)
    row += 1
    _draw_hline(win, row, 0, w - 1, "─", dim)
    row += 1

    # ── Preview de mensajes ───────────────────────────────────────────────
    preview = get_chat_preview(session)
    recent  = preview[-6:] if preview else []
    if not recent:
        _safe_addstr(win, row, 2, "(sin mensajes registrados)", dim)
        row += 1
    else:
        for msg in recent:
            if row >= h - 3:
                break
            if msg["role"] == "user":
                prefix = "👤 "
                attr   = curses.color_pair(_CLR_PREVIEW_U) | curses.A_BOLD
            else:
                prefix = "🤖 "
                attr   = curses.color_pair(_CLR_PREVIEW_A)

            text = msg["text"].strip().replace("\n", " ")
            full = prefix + text
            for wrapped_line in textwrap.wrap(full, w - 3):
                if row >= h - 3:
                    break
                _safe_addstr(win, row, 1, wrapped_line, attr)
                row += 1
            row += 1

    # ── Barra de acciones ─────────────────────────────────────────────────
    action_y = h - 2
    _draw_hline(win, action_y - 1, 0, w - 1, "─", dim)
    in_k = session.get("in_kiro", False)
    in_a = session.get("in_agy",  False)
    agy_hint  = "a→Agy" + ("✓" if in_a else "+ctx")
    kiro_hint = "k→Kiro" + ("✓" if in_k else "+sync")
    hint = f"  [ {agy_hint} ]  [ {kiro_hint} ]  [ Esc volver ]"
    _safe_addstr(win, action_y, 0, hint.ljust(w - 1),
                 curses.color_pair(_CLR_STATUS))


def _run_action_dialog(session):
    """
    Muestra un diálogo de acción simple (sin curses) después de salir del modo TUI.
    Retorna 'back' o 'quit'.
    """
    in_kiro = session.get("in_kiro", False)
    in_agy  = session.get("in_agy",  False)

    while True:
        os.system("clear")

        # Cabecera compacta
        title    = session.get("title", "Sin título")
        tag_str, _ = _tag_str(session)
        sys_tag  = tag_str.strip()
        time_str = format_time(session.get("timestamp", 0))
        cwd      = session.get("cwd", "")

        print(f"\n{C_BOLD}{C_CYAN}  {'─' * 56}{C_RESET}")
        print(f"{C_BOLD}  {title[:54]}{C_RESET}")
        print(f"{C_GRAY}  {sys_tag}  │  {time_str}  │  {cwd[:36]}{C_RESET}")
        print(f"{C_BOLD}{C_CYAN}  {'─' * 56}{C_RESET}\n")

        # Preview corto
        preview = get_chat_preview(session)
        for msg in preview[-4:]:
            if msg["role"] == "user":
                icon, col = "👤", C_CYAN
            else:
                icon, col = "🤖", C_GREEN
            txt = msg["text"].strip().replace("\n", " ")[:60]
            print(f"  {icon} {col}{txt}{C_RESET}")
        if not preview:
            print(f"  {C_GRAY}(sin mensajes){C_RESET}")
        print()

        # Opciones
        if in_agy:
            print(f"  {C_BOLD}{C_GREEN}[a]{C_RESET}  Abrir con Agy   ✅")
        else:
            print(f"  {C_BOLD}{C_GREEN}[a]{C_RESET}  Abrir con Agy   {C_YELLOW}(se inyectará historial de Kiro){C_RESET}")

        if in_kiro:
            print(f"  {C_BOLD}{C_YELLOW}[k]{C_RESET}  Abrir con Kiro  ✅")
        else:
            print(f"  {C_BOLD}{C_YELLOW}[k]{C_RESET}  Abrir con Kiro  {C_CYAN}(se sincronizará historial de Agy){C_RESET}")

        print(f"  {C_BOLD}{C_MAGENTA}[r]{C_RESET}  Renombrar chat")
        print(f"  {C_BOLD}{C_GRAY}[Esc/b]{C_RESET} Volver")
        print(f"  {C_BOLD}{C_RED}[q]{C_RESET}  Salir")
        print()

        try:
            ans = input(f"{C_BOLD}  ¿Qué hacemos? > {C_RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "back"

        if ans == "r":
            try:
                new_title = input(f"\n{C_BOLD}  Nuevo título: {C_RESET}").strip()
                if new_title:
                    if rename_session(session, new_title):
                        session["title"] = new_title
                        print(f"{C_GREEN}  ✅ Guardado. Volviendo...{C_RESET}")
                        import time; time.sleep(1)
            except (EOFError, KeyboardInterrupt):
                pass
            continue

        if ans in ("a", "1"):
            cwd = ensure_cwd(session)
            if not cwd:
                continue
            if in_kiro:
                print(f"\n{C_CYAN}📜 Preparando historial de Kiro para Agy...{C_RESET}")
                sync_kiro_to_agy(session)
                ctx = build_kiro_context(session)
                if ctx:
                    launch_agy(session["id"], cwd, context_prompt=ctx, has_real_agy=in_agy)
                else:
                    launch_agy(session["id"], cwd)
            else:
                launch_agy(session["id"], cwd)

        elif ans in ("k", "2"):
            cwd = ensure_cwd(session)
            if not cwd:
                continue
            if in_agy:
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

        elif ans in ("b", "q", "", "\x1b"):
            return "quit" if ans == "q" else "back"

        else:
            print(f"{C_RED}  Opción no válida.{C_RESET}")
            import time; time.sleep(0.8)


def _tui(stdscr, all_sessions):
    """Función principal del TUI curses."""
    curses.curs_set(0)
    curses.set_escdelay(50)
    _init_colors()
    stdscr.keypad(True)

    sessions     = all_sessions[:]
    filtered     = sessions[:]
    selected     = 0
    scroll_off   = 0
    search_mode  = False
    is_deep_search = False
    search_query = ""
    pending_action = None   # sesión a procesar fuera de curses

    while True:
        h, w = stdscr.getmaxyx()

        # Panel izquierdo: lista (40% del ancho mínimo 30 cols)
        list_w   = max(30, min(w // 2, 52))
        prev_w   = max(1, w - list_w - 1)
        list_h   = h
        prev_h   = h

        # Crear ventanas
        list_win = curses.newwin(list_h, list_w, 0, 0)
        sep_win  = curses.newwin(h, 1, 0, list_w)
        prev_win = curses.newwin(prev_h, prev_w, 0, list_w + 1)

        # Separador vertical
        for row in range(h):
            try:
                sep_win.addstr(row, 0, "│", curses.color_pair(_CLR_DIM))
            except curses.error:
                pass

        # Calcular scroll
        visible = list_h - 3
        if visible < 1:
            visible = 1
        if selected < scroll_off:
            scroll_off = selected
        elif selected >= scroll_off + visible:
            scroll_off = selected - visible + 1

        cur_session = filtered[selected] if filtered else None

        list_win.is_deep_search = is_deep_search
        _draw_list_panel(list_win, filtered, selected, scroll_off, search_query)
        _draw_preview_panel(prev_win, cur_session)

        list_win.noutrefresh()
        sep_win.noutrefresh()
        prev_win.noutrefresh()
        curses.doupdate()

        key = stdscr.getch()

        # ── Modo búsqueda ────────────────────────────────────────────────
        if search_mode:
            if key in (curses.KEY_ENTER, 10, 13, 27):   # Enter / Esc
                search_mode = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                search_query = search_query[:-1]
                q = search_query.lower()
                if q:
                    filtered = search_in_content(sessions, q) if is_deep_search else [s for s in sessions if q in s.get("title", "").lower() or q in s.get("cwd", "").lower()]
                else:
                    filtered = sessions[:]
                selected  = 0
                scroll_off = 0
            elif 32 <= key <= 126:
                search_query += chr(key)
                q = search_query.lower()
                if is_deep_search:
                    filtered = search_in_content(sessions, q)
                else:
                    filtered = [s for s in sessions if q in s.get("title", "").lower() or q in s.get("cwd", "").lower()]
                selected  = 0
                scroll_off = 0
            continue

        # ── Teclas de navegación ─────────────────────────────────────────
        if key == curses.KEY_UP or key == ord('k'):
            if selected > 0:
                selected -= 1

        elif key == curses.KEY_DOWN or key == ord('j'):
            if selected < len(filtered) - 1:
                selected += 1

        elif key == curses.KEY_PPAGE:   # Page Up
            selected = max(0, selected - visible)

        elif key == curses.KEY_NPAGE:   # Page Down
            selected = min(len(filtered) - 1, selected + visible)

        elif key == curses.KEY_HOME or key == ord('g'):
            selected = 0

        elif key == curses.KEY_END or key == ord('G'):
            selected = max(0, len(filtered) - 1)

        elif key == ord('s'):
            search_mode  = True
            is_deep_search = True
            search_query = ""
            filtered     = sessions[:]
            selected     = 0
            scroll_off   = 0

        elif key == ord('/'):
            search_mode  = True
            is_deep_search = False
            search_query = ""
            filtered     = sessions[:]
            selected     = 0
            scroll_off   = 0

        elif key == 27:  # Esc
            if search_query:
                search_query = ""
                filtered     = sessions[:]
                selected     = 0
                scroll_off   = 0

        elif key in (curses.KEY_ENTER, 10, 13, ord('a'), ord('k')) and filtered:
            pending_action = (filtered[selected], key)
            break

        elif key in (ord('q'), ord('Q')):
            pending_action = None
            break

    return pending_action

ERROR_LOG = "/tmp/select_chat_error.log"

def _log_error(msg):
    """Escribe el error en un archivo log para diagnóstico."""
    import traceback as _tb
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        from datetime import datetime as _dt
        f.write(f"\n{'='*60}\n{_dt.now().isoformat()}\n{msg}\n")
        f.write(_tb.format_exc())

def main():
    kiro_list    = get_kiro_sessions()
    agy_list     = get_agy_sessions()
    all_sessions = merge_sessions(kiro_list, agy_list)

    if not all_sessions:
        print(f"{C_RED}No se encontraron conversaciones previas de Kiro ni de Agy.{C_RESET}")
        input("\nPresiona Enter para salir...")
        sys.exit(0)

    while True:
        try:
            result = curses.wrapper(_tui, all_sessions)
        except Exception:
            result = None

        if result is None:
            os.system("clear")
            print("\n  Saliendo...")
            sys.exit(0)

        session, key = result
        in_kiro = session.get("in_kiro", False)
        in_agy  = session.get("in_agy",  False)

        # Si se presionó 'a' o 'k' directamente desde la lista, ir directo
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
                _log_error(str(e))
                os.system("clear")
                print(f"{C_RED}\n❌ Error abriendo con Agy: {e}{C_RESET}")
                print(f"{C_GRAY}  Detalle en: {ERROR_LOG}{C_RESET}")
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
                _log_error(str(e))
                os.system("clear")
                print(f"{C_RED}\n❌ Error abriendo con Kiro: {e}{C_RESET}")
                print(f"{C_GRAY}  Detalle en: {ERROR_LOG}{C_RESET}")
                input("  Presiona Enter para continuar...")

        else:
            # Enter → mostrar diálogo de acción
            try:
                action_result = _run_action_dialog(session)
            except Exception as e:
                _log_error(str(e))
                os.system("clear")
                print(f"{C_RED}\n❌ Error en el diálogo: {e}{C_RESET}")
                print(f"{C_GRAY}  Detalle en: {ERROR_LOG}{C_RESET}")
                input("  Presiona Enter para continuar...")
                continue
            if action_result == "quit":
                os.system("clear")
                print("\n  Saliendo...")
                sys.exit(0)
            # "back" → volver al TUI


# ─── Punto de entrada ─────────────────────────────────────────────────────────

# ─── Punto de entrada ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        os.system("clear")
        print("\n  Saliendo...")
        sys.exit(0)
    except Exception as e:
        # Restaurar terminal por si curses lo dejó sucio
        try:
            import curses as _curses
            _curses.endwin()
        except Exception:
            pass
        os.system("clear")
        _log_error(str(e))
        print(f"{C_RED}\n❌ Error inesperado: {e}{C_RESET}")
        print(f"{C_GRAY}  Detalle en: {ERROR_LOG}{C_RESET}")
        print()
        input("  Presiona Enter para salir...")
        sys.exit(1)

