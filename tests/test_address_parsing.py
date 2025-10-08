from scripts.orchestrate_zip_runs import build_deliverable_rows, extract_address_components


def test_extract_address_components_single_line() -> None:
    street, city, state, postal = extract_address_components(
        {
            "address_lines": ["18808 Brookhurst St. Fountain Valley, CA 92708"],
            "address_text": "18808 Brookhurst St. Fountain Valley, CA 92708",
            "record_zip": None,
            "source_zip": "90001",
        }
    )

    assert street == "18808 Brookhurst St."
    assert city == "Fountain Valley"
    assert state == "CA"
    assert postal == "92708"


def test_extract_address_components_multi_parts() -> None:
    street, city, state, postal = extract_address_components(
        {
            "address_lines": ["4200 Chino Hills Pkwy #600", "Chino Hills, CA 91709"],
            "address_text": "4200 Chino Hills Pkwy #600, Chino Hills, CA 91709",
            "record_zip": None,
            "source_zip": "90001",
        }
    )

    assert street == "4200 Chino Hills Pkwy #600"
    assert city == "Chino Hills"
    assert state == "CA"
    assert postal == "91709"


def test_build_deliverable_rows_address_format() -> None:
    dealers = [
        {
            "dealer_name": "Test Dealer",
            "street": "123 Main St",
            "city": "Los Angeles",
            "state": "CA",
            "postal_code": "90012",
            "phone": "555-0000",
            "website": "example.com",
        }
    ]

    rows = build_deliverable_rows(dealers, list_delimiter="|")
    assert rows == [
        {
            "dealer_name": "Test Dealer",
            "address": "123 Main St, Los Angeles, CA 90012",
            "phone": "555-0000",
            "website": "example.com",
        }
    ]
