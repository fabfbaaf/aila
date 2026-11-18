from importer import load_products


def test_quoted_comma():
    text = 'name,price\n"绿茶, 罐装",12\n红茶,10\n'
    assert load_products(text) == [{"name": "绿茶, 罐装", "price": "12"}, {"name": "红茶", "price": "10"}]
