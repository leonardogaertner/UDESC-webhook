"""
Sistema EMISSOR - ERP / Faturamento

Simula o faturamento de um pedido e, quando o evento "pedido.faturado"
ocorre, envia um webhook (HTTP POST com JSON) para o sistema de expedição.

Uso:
    python emissor.py                       # envia um pedido válido
    python emissor.py --cenario sem_nf      # envia sem o campo nota_fiscal
    python emissor.py --cenario itens_vazios
    python emissor.py --cenario quantidade_zero
    python emissor.py --duplicado           # envia o mesmo evento duas vezes
    python emissor.py --url http://outro-host:8001/webhook/pedidos
"""

import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
URL_PADRAO = os.getenv("WEBHOOK_URL", "http://localhost:8001/webhook/pedidos")
SEGREDO = os.getenv("WEBHOOK_SECRET", "segredo-compartilhado")  # mesmo valor no receptor
TIMEOUT_SEGUNDOS = 5
MAX_TENTATIVAS = 3

CLIENTES = [
    {"codigo": 501, "nome": "Loja Som & Cia", "cidade": "Blumenau/SC"},
    {"codigo": 502, "nome": "Eventos Sul Ltda", "cidade": "Joinville/SC"},
    {"codigo": 503, "nome": "Audio Pro Distribuidora", "cidade": "Curitiba/PR"},
]

PRODUTOS = [
    {"codigo": "CX-200", "descricao": "Caixa ativa 200W", "preco": 1495.00},
    {"codigo": "CX-500", "descricao": "Caixa ativa 500W", "preco": 2890.00},
    {"codigo": "AMP-1K", "descricao": "Amplificador 1000W", "preco": 3250.00},
    {"codigo": "MIC-SF", "descricao": "Microfone sem fio", "preco": 680.00},
]


# ---------------------------------------------------------------------------
# 1. Geração do evento
# ---------------------------------------------------------------------------
def gerar_evento() -> dict:
    """Simula o faturamento de um pedido e monta o payload do evento."""
    itens = []
    for produto in random.sample(PRODUTOS, k=random.randint(1, 3)):
        quantidade = random.randint(1, 10)
        itens.append({
            "codigo": produto["codigo"],
            "descricao": produto["descricao"],
            "quantidade": quantidade,
            "valor_unitario": produto["preco"],
        })

    valor_total = round(sum(i["quantidade"] * i["valor_unitario"] for i in itens), 2)

    return {
        "evento": "pedido.faturado",
        "id_evento": str(uuid.uuid4()),
        "data_hora": datetime.now().isoformat(timespec="seconds"),
        "pedido": {
            "numero": random.randint(10000, 99999),
            "nota_fiscal": f"{random.randint(1, 999999):06d}",
            "cliente": random.choice(CLIENTES),
            "itens": itens,
            "valor_total": valor_total,
        },
    }


def aplicar_cenario(evento: dict, cenario: str) -> dict:
    """Altera o evento para testar as validações do receptor."""
    if cenario == "sem_nf":
        del evento["pedido"]["nota_fiscal"]
    elif cenario == "itens_vazios":
        evento["pedido"]["itens"] = []
        evento["pedido"]["valor_total"] = 0
    elif cenario == "quantidade_zero":
        evento["pedido"]["itens"][0]["quantidade"] = 0
    return evento


# ---------------------------------------------------------------------------
# 2. Envio do webhook
# ---------------------------------------------------------------------------
def assinar(corpo: bytes) -> str:
    """Gera a assinatura HMAC-SHA256 do corpo, para o receptor validar a origem."""
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def enviar_webhook(url: str, evento: dict) -> requests.Response | None:
    """
    Envia o evento via HTTP POST.
    Tenta novamente (com espera crescente) se o receptor estiver fora do ar
    ou responder com erro 5xx. Erros 4xx não são repetidos, pois indicam
    problema nos dados enviados.
    """
    corpo = json.dumps(evento, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Evento": evento["evento"],
        "X-Assinatura": assinar(corpo),
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        print(f"\n→ Tentativa {tentativa}/{MAX_TENTATIVAS}: POST {url}")
        try:
            resposta = requests.post(url, data=corpo, headers=headers, timeout=TIMEOUT_SEGUNDOS)
        except requests.exceptions.ConnectionError:
            print("  ✗ Não foi possível conectar ao receptor (está rodando?).")
        except requests.exceptions.Timeout:
            print(f"  ✗ O receptor não respondeu em {TIMEOUT_SEGUNDOS}s.")
        else:
            if resposta.status_code < 500:
                return resposta
            print(f"  ✗ Erro no servidor receptor (HTTP {resposta.status_code}).")

        if tentativa < MAX_TENTATIVAS:
            espera = 2 ** (tentativa - 1)  # 1s, 2s, 4s...
            print(f"  Aguardando {espera}s para tentar novamente...")
            time.sleep(espera)

    return None


# ---------------------------------------------------------------------------
# 3. Exibição do resultado
# ---------------------------------------------------------------------------
def exibir_resultado(evento: dict, resposta: requests.Response | None) -> bool:
    print("\n" + "=" * 60)
    if resposta is None:
        print("RESULTADO: FALHA — webhook não entregue após todas as tentativas.")
        print("=" * 60)
        return False

    print(f"Status HTTP: {resposta.status_code} {resposta.reason}")
    try:
        corpo = resposta.json()
        print("Resposta do receptor:")
        print(json.dumps(corpo, indent=2, ensure_ascii=False))
    except ValueError:
        corpo = None
        print(f"Resposta do receptor (texto): {resposta.text}")

    sucesso = resposta.ok
    print("-" * 60)
    if sucesso:
        print("RESULTADO: SUCESSO — evento entregue e aceito pelo receptor.")
        # Confere se o receptor confirmou o mesmo evento que foi enviado
        if isinstance(corpo, dict) and "id_evento" in corpo:
            confere = corpo["id_evento"] == evento["id_evento"]
            print(f"Conferência do id_evento: {'OK' if confere else 'DIVERGENTE'}")
    else:
        print("RESULTADO: FALHA — o receptor recusou o evento.")
    print("=" * 60)
    return sucesso


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Emissor do webhook pedido.faturado")
    parser.add_argument("--url", default=URL_PADRAO, help="endpoint do receptor")
    parser.add_argument(
        "--cenario",
        default="valido",
        choices=["valido", "sem_nf", "itens_vazios", "quantidade_zero"],
        help="tipo de payload a enviar (para testes)",
    )
    parser.add_argument("--duplicado", action="store_true",
                        help="envia o mesmo evento duas vezes (teste de idempotência)")
    args = parser.parse_args()

    print("=" * 60)
    print("EMISSOR — ERP / Faturamento")
    print("=" * 60)

    evento = aplicar_cenario(gerar_evento(), args.cenario)
    print(f"Evento gerado: {evento['evento']} | cenário: {args.cenario}")
    print("Payload JSON enviado:")
    print(json.dumps(evento, indent=2, ensure_ascii=False))

    resposta = enviar_webhook(args.url, evento)
    sucesso = exibir_resultado(evento, resposta)

    if args.duplicado:
        print("\nReenviando o MESMO evento (mesmo id_evento)...")
        resposta = enviar_webhook(args.url, evento)
        exibir_resultado(evento, resposta)

    return 0 if sucesso else 1


if __name__ == "__main__":
    sys.exit(main())
