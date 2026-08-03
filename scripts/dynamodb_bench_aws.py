"""AWS DynamoDB resource lifecycle + low-level client, for Phase 8
benchmarking only. boto3 is imported here and nowhere under src/dynamokv --
that boundary must never be crossed just because this phase exists.

Table lifecycle rule: this module NEVER attaches an Application Auto
Scaling policy to the benchmark table. Leaving the table on plain
PROVISIONED capacity (no autoscaling) is what keeps it pinned at its
configured RCU/WCU forever and genuinely $0 -- exceeding capacity just
throttles (ProvisionedThroughputExceededException), it does not bill for
overage. Do not "improve" this by adding autoscaling.
"""
import socket
import ssl
import time
from typing import Dict, List, Optional

import boto3
from bench_config import AWS_MAX_ATTEMPTS, AWS_REGION, CONNECTION_POOL_SIZE
from botocore.config import Config


def build_client():
    """Low-level client, not resource() -- the resource API's
    TypeSerializer/TypeDeserializer convenience layer adds its own
    client-side marshaling overhead that would be an unaccounted asymmetry
    against our system's much thinner JSON body."""
    config = Config(
        region_name=AWS_REGION,
        retries={"max_attempts": AWS_MAX_ATTEMPTS, "mode": "standard"},
        max_pool_connections=CONNECTION_POOL_SIZE,
    )
    return boto3.client("dynamodb", config=config)


def table_exists(client, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def create_table_if_missing(client, table_name: str, rcu: int, wcu: int) -> bool:
    """Returns True if a table was actually created, False if one already existed."""
    if table_exists(client, table_name):
        return False
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": rcu, "WriteCapacityUnits": wcu},
    )
    wait_until_active(client, table_name)
    return True


def wait_until_active(client, table_name: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.describe_table(TableName=table_name)
        if resp["Table"]["TableStatus"] == "ACTIVE":
            return
        time.sleep(2)
    raise TimeoutError(f"Table {table_name!r} did not become ACTIVE within {timeout}s")


def teardown_table(client, table_name: str) -> None:
    if table_exists(client, table_name):
        client.delete_table(TableName=table_name)


def put_item(client, table_name: str, key: str, value: str) -> None:
    client.put_item(TableName=table_name, Item={"pk": {"S": key}, "value": {"S": value}})


def get_item(client, table_name: str, key: str, consistent_read: bool) -> Optional[str]:
    resp = client.get_item(TableName=table_name, Key={"pk": {"S": key}}, ConsistentRead=consistent_read)
    item = resp.get("Item")
    return item["value"]["S"] if item else None


def delete_item(client, table_name: str, key: str) -> None:
    client.delete_item(TableName=table_name, Key={"pk": {"S": key}})


def measure_network_rtt(host: str, port: int = 443, timeout: float = 5.0) -> Dict[str, float]:
    """Pure network RTT to the DynamoDB endpoint -- raw TCP connect + TLS
    handshake timing, no DynamoDB API call at all. Lets the report show how
    much of DynamoDB's measured operation latency is WAN transit vs.
    server-side processing, without needing a same-region EC2 instance."""
    t0 = time.perf_counter()
    sock = socket.create_connection((host, port), timeout=timeout)
    t1 = time.perf_counter()
    ctx = ssl.create_default_context()
    ssock = ctx.wrap_socket(sock, server_hostname=host)
    t2 = time.perf_counter()
    ssock.close()
    return {
        "tcp_connect_ms": (t1 - t0) * 1000,
        "tls_handshake_ms": (t2 - t1) * 1000,
        "total_ms": (t2 - t0) * 1000,
    }


def run_network_rtt_probe(host: str, n_samples: int, port: int = 443, timeout: float = 5.0) -> Dict[str, List[float]]:
    tcp_ms, tls_ms, total_ms = [], [], []
    for _ in range(n_samples):
        sample = measure_network_rtt(host, port=port, timeout=timeout)
        tcp_ms.append(sample["tcp_connect_ms"])
        tls_ms.append(sample["tls_handshake_ms"])
        total_ms.append(sample["total_ms"])
    return {"tcp_connect_ms": tcp_ms, "tls_handshake_ms": tls_ms, "total_ms": total_ms}
