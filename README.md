# Webhook `pedido.faturado` — ERP → Expedição

Duas aplicações em Python que se comunicam por webhook (HTTP POST com JSON).

- **Emissor (`emissor/`)** — simula o ERP: quando um pedido é faturado, monta o evento
  `pedido.faturado` em JSON, assina o corpo com HMAC-SHA256 e envia ao receptor.
  Repete o envio (até 3 vezes) se o receptor estiver fora do ar ou responder 5xx.
- **Receptor (`receptor/`)** — simula a expedição: endpoint FastAPI que confere a
  assinatura, interpreta e valida o JSON, recusa eventos duplicados, coloca o pedido
  na fila de separação e responde com sucesso ou falha.

## Como executar

Requisitos: Python 3.10+.

```bash
# Terminal 1 — receptor (porta 8001)
cd receptor
pip install -r requirements.txt
python receptor.py

# Terminal 2 — emissor
cd emissor
pip install -r requirements.txt
python emissor.py
```

Outros cenários de teste:

```bash
python emissor.py --cenario sem_nf           # 422: campo obrigatório ausente
python emissor.py --cenario itens_vazios     # 422: lista de itens vazia
python emissor.py --cenario quantidade_zero  # 422: quantidade inválida
python emissor.py --duplicado                # 200 e depois 409: evento repetido
```

Pedidos recebidos: <http://localhost:8001/pedidos> · Documentação do endpoint: <http://localhost:8001/docs>

## Respostas do receptor

| Código | Situação |
|---|---|
| 200 | Evento válido, processado e colocado na fila |
| 400 | Corpo não é um JSON válido |
| 401 | Assinatura ausente ou inválida |
| 409 | `id_evento` já recebido |
| 422 | Campo obrigatório ausente ou valor inválido |

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `WEBHOOK_URL` | `http://localhost:8001/webhook/pedidos` | Endpoint para onde o emissor envia |
| `WEBHOOK_SECRET` | `segredo-compartilhado` | Chave da assinatura HMAC (igual nos dois sistemas) |
