"""
Sistema RECEPTOR - Expedição / Logística

Expõe um endpoint HTTP que recebe o webhook "pedido.faturado" enviado pelo
ERP, valida a origem (assinatura HMAC) e os dados (JSON e campos
obrigatórios), registra o pedido na fila de separação e responde
indicando sucesso ou falha.

Uso:
    uvicorn receptor:app --port 8001
    (ou)  python receptor.py

Endpoints:
    POST /webhook/pedidos   recebe o evento
    GET  /pedidos           lista os pedidos já recebidos (fila de separação)
"""

import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

SEGREDO = os.getenv("WEBHOOK_SECRET", "segredo-compartilhado")  # mesmo valor do emissor

app = FastAPI(title="Receptor - Expedição")

# "Banco de dados" em memória: id_evento -> registro do pedido
fila_separacao: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Contrato do JSON (campos obrigatórios e regras de validação)
# ---------------------------------------------------------------------------
class Cliente(BaseModel):
    codigo: int
    nome: str = Field(min_length=1)
    cidade: str | None = None


class Item(BaseModel):
    codigo: str = Field(min_length=1)
    descricao: str | None = None
    quantidade: int = Field(gt=0)
    valor_unitario: float | None = Field(default=None, ge=0)


class Pedido(BaseModel):
    numero: int
    nota_fiscal: str = Field(min_length=1)
    cliente: Cliente
    itens: list[Item] = Field(min_length=1)
    valor_total: float = Field(ge=0)


class EventoPedidoFaturado(BaseModel):
    evento: Literal["pedido.faturado"]
    id_evento: str = Field(min_length=1)
    data_hora: datetime
    pedido: Pedido


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------
def falha(status: int, mensagem: str, erros: list | None = None) -> JSONResponse:
    corpo = {"status": "falha", "mensagem": mensagem}
    if erros:
        corpo["erros"] = erros
    print(f"  ✗ HTTP {status}: {mensagem}" + (f" {erros}" if erros else ""))
    return JSONResponse(status_code=status, content=corpo)


def assinatura_valida(corpo: bytes, assinatura: str | None) -> bool:
    if not assinatura:
        return False
    esperada = "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, assinatura)


def traduzir_erros(erro: ValidationError) -> list[str]:
    """Converte os erros do Pydantic em mensagens legíveis: 'campo: problema'."""
    mensagens = []
    for e in erro.errors():
        campo = ".".join(str(p) for p in e["loc"])
        ctx = e.get("ctx", {})
        traducoes = {
            "missing": "campo obrigatório ausente",
            "too_short": "não pode estar vazio",
            "greater_than": f"deve ser maior que {ctx.get('gt')}",
            "greater_than_equal": f"deve ser maior ou igual a {ctx.get('ge')}",
            "literal_error": f"valor deve ser {ctx.get('expected')}",
            "int_parsing": "deve ser um número inteiro",
            "float_parsing": "deve ser um número",
            "string_type": "deve ser um texto",
            "datetime_from_date_parsing": "data/hora inválida",
            "datetime_parsing": "data/hora inválida",
        }
        mensagens.append(f"{campo}: {traducoes.get(e['type'], e['msg'])}")
    return mensagens


def exibir_pedido(evento: EventoPedidoFaturado) -> None:
    p = evento.pedido
    print("  ✓ Pedido recebido e validado:")
    print(f"    Evento.......: {evento.evento} ({evento.id_evento})")
    print(f"    Data/hora....: {evento.data_hora}")
    print(f"    Pedido / NF..: {p.numero} / {p.nota_fiscal}")
    print(f"    Cliente......: {p.cliente.codigo} - {p.cliente.nome} ({p.cliente.cidade})")
    for item in p.itens:
        print(f"    Item.........: {item.quantidade}x {item.codigo} - {item.descricao}")
    valor = f"{p.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print(f"    Valor total..: R$ {valor}")


# ---------------------------------------------------------------------------
# Endpoint do webhook
# ---------------------------------------------------------------------------
@app.post("/webhook/pedidos")
async def receber_pedido(request: Request):
    corpo = await request.body()
    print(f"\n[{datetime.now():%H:%M:%S}] Webhook recebido de {request.client.host}")

    # 1. Origem: a assinatura HMAC confirma que quem enviou conhece o segredo
    #    e que o corpo não foi alterado no caminho.
    if not assinatura_valida(corpo, request.headers.get("X-Assinatura")):
        return falha(401, "Assinatura inválida ou ausente.")

    # 2. Interpretar o JSON
    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        return falha(400, "O corpo da requisição não é um JSON válido.")

    # 3. Validar campos obrigatórios e regras
    try:
        evento = EventoPedidoFaturado.model_validate(dados)
    except ValidationError as e:
        return falha(422, "Dados do evento inválidos.", traduzir_erros(e))

    # 4. Idempotência: o mesmo evento não pode ser processado duas vezes
    if evento.id_evento in fila_separacao:
        return falha(409, f"Evento {evento.id_evento} já foi recebido anteriormente.")

    # 5. Processar: exibir e colocar o pedido na fila de separação
    exibir_pedido(evento)
    fila_separacao[evento.id_evento] = {
        "recebido_em": datetime.now().isoformat(timespec="seconds"),
        "situacao": "aguardando separação",
        **evento.model_dump(mode="json"),
    }
    print(f"  → Pedido {evento.pedido.numero} adicionado à fila de separação "
          f"({len(fila_separacao)} na fila).")

    return {
        "status": "sucesso",
        "mensagem": f"Pedido {evento.pedido.numero} recebido e enviado para separação.",
        "id_evento": evento.id_evento,
        "pedido": evento.pedido.numero,
        "nota_fiscal": evento.pedido.nota_fiscal,
        "quantidade_itens": sum(i.quantidade for i in evento.pedido.itens),
        "valor_total": evento.pedido.valor_total,
    }


@app.get("/pedidos")
def listar_pedidos():
    """Mostra o que já foi recebido — útil para conferir os dados na demonstração."""
    return {"total": len(fila_separacao), "pedidos": list(fila_separacao.values())}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
