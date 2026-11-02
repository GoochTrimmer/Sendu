from sourcer.normalize import contains_term, normalize, parse_terms


def test_nfkc_fullwidth_and_case():
    assert normalize("ＹＯＨＪＩ　ＹＡＭＡＭＯＴＯ") == "yohji yamamoto"
    assert normalize("サイズ３") == "サイズ3"


def test_ys_variants_unified():
    for raw in ["Y's", "Y’s", "y＇s", "Ys", "ｙ’ｓ", "Y 's"]:
        assert normalize(raw) == "ys", raw


def test_whitespace_collapsed():
    assert normalize("  pour \n\t homme  ") == "pour homme"


def test_latin_terms_use_word_boundaries():
    text = normalize("Holidays sale Ys skirt")
    assert contains_term(text, "Y's")
    assert not contains_term(normalize("holidays"), "ys")
    assert contains_term(normalize("Yohji Yamamoto POUR HOMME"), "pour homme")
    assert not contains_term(normalize("replicant"), "replica")


def test_japanese_terms_match_as_substrings():
    text = normalize("ヨウジヤマモトプールオムジャケット")
    assert contains_term(text, "プールオム")
    assert contains_term(text, "ヨウジヤマモト")


def test_parse_terms():
    assert parse_terms("a, b\nc、d ,, ") == ["a", "b", "c", "d"]
    assert parse_terms(None) == []
