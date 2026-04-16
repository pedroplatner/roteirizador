' Abre o Roteirizador sem mostrar janela de terminal
Dim pasta
pasta = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

Dim ws
Set ws = CreateObject("WScript.Shell")

' Inicia o Streamlit em segundo plano (janela oculta)
ws.Run "cmd /c cd /d """ & pasta & """ && streamlit run grok2.py --server.headless true", 0, False

' Aguarda 3 segundos para o servidor subir e abre o navegador
WScript.Sleep 3000
ws.Run "http://localhost:8501"
