from importer import load_products


def test_escaped_quote_and_blank_lines():
    text = 'name, price\n\n"说""明书",3\n\n'
    assert load_products(text) == [{"name": '说"明书', "price": "3"}]


def test_header_only():
    assert load_products("name,price\n") == []
