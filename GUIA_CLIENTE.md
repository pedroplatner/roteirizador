# Roteirizador — Guia do Usuário

---

## INSTALAÇÃO (fazer uma única vez)

### 1. Instalar o Python

Acesse **python.org/downloads**, baixe a versão mais recente e instale.

> ⚠️ **IMPORTANTE:** durante a instalação, marque a opção **"Add Python to PATH"**

---

### 2. Baixar o sistema

Abra o **CMD** (tecla Windows + R → digite `cmd` → Enter) e cole os comandos abaixo um por vez:

```
git clone https://github.com/pedroplatner/roteirizador.git
cd roteirizador
pip install -r requirements.txt
```

Aguarde a instalação terminar (pode demorar alguns minutos).

---

### 3. Criar o atalho

1. Abra a pasta `roteirizador`
2. Clique com o botão direito no arquivo **`iniciar_silencioso.vbs`**
3. Selecione **"Enviar para" → "Área de trabalho (criar atalho)"**
4. Renomeie o atalho para **Roteirizador**

A partir de agora é só dar dois cliques no atalho da área de trabalho.

---

## ABRINDO O SISTEMA

1. Clique duas vezes no atalho **Roteirizador** na área de trabalho
2. Aguarde alguns segundos — o navegador abre sozinho
3. O sistema estará disponível em: **http://localhost:8501**

> O sistema roda localmente no seu computador — não precisa de internet para usar (somente para buscar GPS de endereços novos).

---

## COMO USAR

### Carregar sua planilha

Na sidebar (painel lateral esquerdo), clique em **"📂 Carregar Excel"** e selecione o arquivo `.xlsx` com os dados das rotas.

### Abas principais

| Aba | O que faz |
|-----|-----------|
| 📊 **Geral** | Visualiza e edita todos os passageiros |
| 🆕 **Novos** | Gerencia passageiros sem rota definida |
| 🚨 **Erros** | Corrige endereços e coordenadas com problema |
| 📲 **Export** | Exporta o arquivo Excel atualizado |
| 📜 **Histórico** | Registro de todas as alterações feitas |
| ⚡ **Otimização** | Ferramentas de roteirização automática |
| 🗺️ **Rotas** | Simula e aplica horários por rota |

### Mapa

- **🏠 Casas** — exibe a localização residencial dos passageiros
- **🚌 Pontos** — exibe os pontos de embarque
- Clique em um ponto no mapa para mover o embarque de um passageiro

---

## ATUALIZAÇÕES

Quando houver uma atualização do sistema, abra o CMD na pasta `roteirizador` e rode:

```
git pull
```

Depois abra o sistema normalmente pelo atalho.

> Você também pode criar um atalho do arquivo **`atualizar.bat`** na área de trabalho para facilitar.

---

## LICENÇA

O sistema possui um período de uso vinculado a este computador.

- O status da licença aparece na sidebar em **"🔑 Licença"**
- Quando a licença estiver próxima do vencimento, aparecerá um aviso em amarelo
- Quando expirar, o sistema exibirá uma tela de bloqueio com o **ID desta máquina**

### O que fazer quando a licença expirar

1. Abra o sistema — aparecerá a tela de bloqueio
2. Copie o **ID da máquina** que aparece na tela
3. Envie o ID para o suporte por WhatsApp ou e-mail
4. Aguarde o código de liberação
5. Digite o código no campo indicado e pressione Enter

---

## SUPORTE

Em caso de dúvidas ou problemas, entre em contato:

**WhatsApp:** (41) 99535-2485
**E-mail:** platnersystem@gmail.com

---

## PROBLEMAS COMUNS

**O navegador não abre sozinho**
→ Abra manualmente e acesse: http://localhost:8501

**Aparece erro "Module not found"**
→ Abra o CMD na pasta `roteirizador` e rode: `pip install -r requirements.txt`

**O sistema ficou lento ou travou**
→ Feche o navegador, aguarde 10 segundos e abra o atalho novamente

**Perdi o atalho da área de trabalho**
→ Vá até a pasta `roteirizador`, clique com botão direito em `iniciar_silencioso.vbs` e recrie o atalho

---

*Roteirizador V28*
