# trial_guard.py — sistema de licença com controle remoto via GitHub Gist
import json
import os
import hashlib
import platform
import requests
from datetime import date, timedelta
from pathlib import Path

APP_NAME = "Roteirizador"
TRIAL_DAYS_PADRAO = 30

# Senha para liberar manualmente quando offline (env var ou fallback)
SENHA_MESTRA = os.environ.get("ROTEIRIZADOR_KEY", "Binho@Rot2025#!")

# ─────────────────────────────────────────────────────────────────────────────
# COLE AQUI A URL RAW DO SEU GIST depois de criá-lo (instruções no README)
# Exemplo: https://gist.githubusercontent.com/pedroplatner/XXXX/raw/licencas.json
LICENCAS_URL = ""
# ─────────────────────────────────────────────────────────────────────────────


def machine_id() -> str:
    raw = f"{platform.node()}|{platform.system()}|{platform.release()}|{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


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


def _save_state(state: dict) -> None:
    _state_path().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_date(s: str):
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except:
        return None


def _set_license(dias: int, tipo: str = "trial") -> dict:
    state = _load_state()
    hoje = date.today()
    exp = hoje + timedelta(days=int(dias))
    state.update({
        "machine_id": machine_id(),
        "inicio": state.get("inicio") or hoje.isoformat(),
        "expira": exp.isoformat(),
        "last_run": hoje.isoformat(),
        "tipo": tipo,
    })
    _save_state(state)
    return state


def _check_remote(mid: str) -> dict | None:
    """
    Busca licencas.json no GitHub Gist.
    Retorna o dict da máquina ou None (sem internet / não cadastrado).
    """
    if not LICENCAS_URL:
        return None
    try:
        r = requests.get(LICENCAS_URL, timeout=5)
        if r.status_code == 200:
            maquinas = r.json().get("maquinas", {})
            return maquinas.get(mid)
    except:
        pass
    return None


def get_status() -> dict:
    """Retorna dict para exibir na sidebar do app."""
    state = _load_state()
    mid = machine_id()
    exp = _parse_date(state.get("expira", ""))
    hoje = date.today()
    ativo = bool(exp and hoje <= exp)
    dias = (exp - hoje).days if (exp and ativo) else 0
    return {
        "ativo": ativo,
        "dias_restantes": dias,
        "expira": state.get("expira", "—"),
        "machine_id": mid,
        "tipo": state.get("tipo", "trial"),
    }


def validar_ou_bloquear(st_module):
    """
    Chame no topo do grok2.py: validar_ou_bloquear(st)
    Retorna True se liberado. Trava o app com st.stop() se bloqueado.
    """
    state = _load_state()
    mid = machine_id()
    hoje = date.today()

    # Primeira execução → inicia trial automático
    if not state.get("expira"):
        state = _set_license(TRIAL_DAYS_PADRAO, "trial")

    # Anti "voltar relógio"
    last_run = _parse_date(state.get("last_run", ""))
    if last_run and hoje < last_run:
        st_module.error("⚠️ Relógio do sistema foi alterado. Contate o suporte.")
        st_module.info(f"ID desta máquina: `{mid}`")
        st_module.stop()

    state["last_run"] = hoje.isoformat()
    _save_state(state)

    # ── Verifica licença REMOTA (se LICENCAS_URL estiver configurada) ──
    remota = _check_remote(mid)
    if remota is not None:
        if not remota.get("ativo", True):
            # Dono desativou essa máquina no gist
            st_module.error("🔒 Licença desativada pelo administrador.")
            st_module.info(f"ID desta máquina: `{mid}`")
            st_module.stop()

        exp_remota = _parse_date(remota.get("expira", ""))
        if exp_remota:
            if hoje <= exp_remota:
                # Atualiza cache local com dados do gist
                state["expira"] = remota["expira"]
                state["tipo"] = "licenciado"
                _save_state(state)
                return True
            else:
                # Gist diz que expirou → bloqueia mesmo com cache local válido
                _mostrar_tela_bloqueio(st_module, mid)

    # ── Verifica licença LOCAL (offline ou sem gist configurado) ──
    exp = _parse_date(state.get("expira", ""))
    if exp and hoje <= exp:
        return True

    _mostrar_tela_bloqueio(st_module, mid)


def _mostrar_tela_bloqueio(st_module, mid: str):
    st_module.title("🔒 Licença encerrada")
    st_module.markdown(
        f"""
        **ID desta máquina:** `{mid}`

        Envie este código para o administrador e solicite um código de liberação.
        """
    )
    codigo = st_module.text_input("Código de liberação", type="password")
    if codigo:
        for dias in [30, 60, 90, 180, 365]:
            if codigo == f"{SENHA_MESTRA}|{dias}":
                _set_license(dias, "licenciado")
                st_module.success(f"✅ Liberado por {dias} dias! Recarregando...")
                st_module.rerun()
        st_module.error("❌ Código inválido.")
    st_module.stop()
