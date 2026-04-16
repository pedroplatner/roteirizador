# Roteirizador — Guia Operacional Completo
**Versão:** V28 | **Dono do sistema:** Pedro Platner

---

## VISÃO GERAL

O sistema funciona assim:

```
VOCÊ (dono)                     CLIENTE
    |                               |
    | 1. Instala o app no PC dele   |
    |------------------------------>|
    |                               | 2. Abre com iniciar_silencioso.vbs
    |                               | 3. App roda 30 dias grátis (trial)
    |                               |
    | 4. Pega o ID da máquina dele  |
    |<------------------------------|
    |                               |
    | 5. Cadastra no Gist (online)  |
    | 6. Renova/desativa quando     |
    |    quiser                     |
    |------------------------------>| App atualiza sozinho na próxima abertura
```

---

## PARTE 1 — INSTALAR NO PC DO CLIENTE (fazer uma única vez)

### Pré-requisito: Python instalado

O cliente precisa ter Python 3.10 ou superior.
- Download: https://www.python.org/downloads/
- **IMPORTANTE:** na instalação, marcar "Add Python to PATH"

### Passo a passo de instalação

Você ou o cliente executa estes comandos no terminal (CMD):

```cmd
# 1. Baixar o sistema do GitHub
git clone https://github.com/pedroplatner/roteirizador.git
cd roteirizador

# 2. Instalar dependências (só uma vez)
pip install -r requirements.txt

# 3. Testar se funciona
streamlit run grok2.py
```

Se abriu no navegador → instalado com sucesso. Feche o terminal.

### Criar atalho na área de trabalho

1. Clique com botão direito no arquivo `iniciar_silencioso.vbs`
2. "Enviar para" → "Área de trabalho (criar atalho)"
3. Renomeie o atalho para "Roteirizador"
4. (Opcional) troque o ícone: botão direito → Propriedades → Alterar ícone

A partir de agora o cliente abre o app com dois cliques no atalho.

---

## PARTE 2 — IDENTIFICAR O ID DA MÁQUINA DO CLIENTE

O ID da máquina é um código único de 12 caracteres gerado a partir do hardware do PC.
Exemplo: `a3f9c12b8e41`

### Como o cliente te envia o ID

**Opção A — Pela sidebar do app (mais fácil):**
1. Cliente abre o app
2. Clica no `>` da sidebar (canto superior esquerdo)
3. Rola para baixo até o expander **"🔑 Licença"**
4. O ID aparece em destaque — é só copiar e mandar por WhatsApp

**Opção B — Quando o trial expira:**
- Quando os 30 dias acabam, o app mostra automaticamente uma tela com o ID
- O cliente copia o código da tela e te manda

---

## PARTE 3 — CONTROLE DE LICENÇAS (painel do dono)

Tudo é controlado por um arquivo JSON no GitHub Gist.

### Onde fica o painel

URL do seu gist:
```
https://gist.github.com/pedroplatner/feee90d324a59d7553c3d73bacaf7878
```

Acesse, clique no lápis (✏️) para editar.

### Formato do arquivo licencas.json

```json
{
  "maquinas": {
    "ID_DA_MAQUINA_1": {
      "nome": "Transportadora ABC",
      "expira": "2025-12-31",
      "ativo": true
    },
    "ID_DA_MAQUINA_2": {
      "nome": "Empresa XYZ",
      "expira": "2025-06-30",
      "ativo": true
    }
  }
}
```

### Ações comuns

#### ✅ Ativar cliente novo
Adicione uma entrada com o ID que ele te mandou:
```json
"a3f9c12b8e41": {
  "nome": "Nome do Cliente",
  "expira": "2026-04-16",
  "ativo": true
}
```
Salve o gist. Na próxima abertura do app no PC dele, já reconhece.

#### ❌ Desativar cliente (ex: cancelou, inadimplência)
Mude `"ativo": true` para `"ativo": false`:
```json
"a3f9c12b8e41": {
  "nome": "Nome do Cliente",
  "expira": "2026-04-16",
  "ativo": false
}
```
Salve. Na próxima abertura, o app bloqueia com mensagem "Licença desativada pelo administrador."

#### 🔄 Renovar licença
Mude apenas a data:
```json
"expira": "2027-04-16"
```

#### 📅 Liberar manualmente por código (sem internet)
Se o cliente estiver offline, você pode gerar um código manual.

Formato: `Binho@Rot2025#!|DIAS`

| Código | Libera por |
|--------|-----------|
| `Binho@Rot2025#!\|30` | 30 dias |
| `Binho@Rot2025#!\|60` | 60 dias |
| `Binho@Rot2025#!\|90` | 90 dias |
| `Binho@Rot2025#!\|180` | 6 meses |
| `Binho@Rot2025#!\|365` | 1 ano |

O cliente digita esse código na tela de bloqueio.

**⚠️ Não compartilhe essa senha com ninguém.**

---

## PARTE 4 — ENVIAR ATUALIZAÇÕES DO SISTEMA

### Quando você corrige um bug ou adiciona função

No seu PC, dentro da pasta do projeto:

```cmd
cd e:\Projetos\Binho
git add grok2.py otimizador.py
git commit -m "Descrição da mudança"
git push
```

### O cliente atualiza

```cmd
cd roteirizador
git pull
```

Pronto. A próxima abertura do app já tem a versão nova.

> **Dica:** você pode pedir ao cliente para rodar `git pull` antes de abrir,
> ou criar um `atualizar.bat` com esse comando.

### Criar atualizar.bat para o cliente

Crie um arquivo `atualizar.bat` na pasta do cliente com:
```bat
@echo off
cd /d "%~dp0"
git pull
echo Atualização concluída! Pode abrir o Roteirizador.
pause
```

---

## PARTE 5 — FLUXO RESUMIDO DO DIA A DIA

```
NOVO CLIENTE:
  1. Instala Python no PC dele
  2. Clona o repo: git clone https://github.com/pedroplatner/roteirizador.git
  3. pip install -r requirements.txt
  4. Cria atalho do iniciar_silencioso.vbs
  5. Ele usa por 30 dias (trial automático)
  6. No fim do trial OU quando quiser ativar antes:
     → Pede o ID da máquina (sidebar → 🔑 Licença)
     → Você adiciona no gist com data de expiração
     → App libera automaticamente

RENOVAR:
  → Gist → alterar "expira" → salvar
  → Sem precisar fazer nada no PC do cliente

DESATIVAR:
  → Gist → "ativo": false → salvar
  → Na próxima abertura já bloqueia

ATUALIZAR O SISTEMA:
  → Você: git push
  → Cliente: git pull (ou abre o atualizar.bat)
```

---

## PARTE 6 — REFERÊNCIA RÁPIDA

| O que | Onde |
|-------|------|
| Código do sistema | https://github.com/pedroplatner/roteirizador |
| Painel de licenças | https://gist.github.com/pedroplatner/feee90d324a59d7553c3d73bacaf7878 |
| Senha manual | `Binho@Rot2025#!` + `\|DIAS` |
| Onde fica o ID da máquina | Sidebar do app → "🔑 Licença" |
| Trial padrão | 30 dias automáticos |

---

*Documento gerado em 2026-04-16*
