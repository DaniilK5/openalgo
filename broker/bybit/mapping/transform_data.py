def map_exchange_type(exchange: str) -> str:
    return "CRYPTO" if exchange == "CRYPTO" else exchange


def map_product_type(product: str) -> str:
    mapping = {"CNC": "linear", "NRML": "linear", "MIS": "linear"}
    return mapping.get(product, "linear")


def reverse_map_product_type(product: str) -> str:
    return product


def transform_data(data):
    return data


def transform_modify_order_data(data):
    return data
