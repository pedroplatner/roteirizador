# trial_guard.py
import json
import os
import hashlib
import platform
from datetime import date, timedelta
from pathlib import Path

APP_NAME = "Roteirizador"
TRIAL_DAYS_PADRAO = 30

# Senha de liberação — leia do ambiente ou use o fallback local.
# Em produção: defina a variável de ambiente ROTEIRIZADOR_KEY antes de subir.
import os as _os
SENHA_MESTRA = _os.environ.get("ROTEIRIZADOR_KEY", "Binho@Rot2025#!")

def _data_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home())))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d

def _state_path() -> Path:
    return _data_dir() / "estado_trial.json"

def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except:
        return {}

def _save_state(st: dict) -> None:
    _state_path().write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

def machine_id() -> str:
    # “Assinatura” simples do PC (MVP). Não é perfeita, mas serve.
    raw = f"{platform.node()}|{platform.system()}|{platform.release()}|{platform.machine()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

def _today_iso() -> str:
    return date.today().isoformat()

def _parse_date(s: str) -> date | None:
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except:
        return None

def _set_trial(dias: int) -> dict:
    st = _load_state()
    hoje = date.today()
    exp = hoje + timedelta(days=int(dias))
    st.update({
        "machine_id": machine_id(),
        "inicio": st.get("inicio") or hoje.isoformat(),
        "expira": exp.isoformat(),
        "last_run": hoje.isoformat(),
    })
    _save_state(st)
    return st

def validar_ou_bloquear(st_module):
    """
    st_module = streamlit (passa o próprio st)
    Retorna True se liberado, senão mostra tela e trava (st.stop()).
    """
    st = _load_state()
    hoje = date.today()

    # primeira execução: cria trial
    if not st.get("expira"):
        st = _set_trial(TRIAL_DAYS_PADRAO)

    # anti “voltar relógio” (bem básico)
    last_run = _parse_date(st.get("last_run", ""))
    if last_run and hoje < last_run:
        st_module.error("Relógio do sistema foi alterado. Contate o suporte.")
        st_module.stop()

    # atualiza last_run
    st["last_run"] = hoje.isoformat()
    _save_state(st)

    exp = _parse_date(st.get("expira", ""))
    if not exp:
        st_module.error("Estado de licença inválido. Contate o suporte.")
        st_module.stop()

    if hoje <= exp:
        return True

    # Expirou -> pede código
    st_module.title("🔒 Período de teste encerrado")
    st_module.write(f"Este computador: `{machine_id()}`")
    st_module.write("Para continuar, insira o **código de liberação**.")

    codigo = st_module.text_input("Código", type="password")

    # MVP: duas senhas “modo dono”
    # Ex.: SENHA_MESTRA + "|30" ou SENHA_MESTRA + "|365"
    if codigo:
        if codigo == f"{SENHA_MESTRA}|30":
            _set_trial(30)
            st_module.success("Liberado por 30 dias. Recarregue a página.")
            st_module.stop()
        if codigo == f"{SENHA_MESTRA}|365":
            _set_trial(365)
            st_module.success("Liberado por 365 dias. Recarregue a página.")
            st_module.stop()

        st_module.error("Código inválido.")

    st_module.stop()
