"""scripts/identify_test_accounts.py: classification rules, and that the script cannot write. No backend needed."""
import re
import sys
from pathlib import Path

import bcrypt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import identify_test_accounts as ita  # noqa: E402

SCRIPT = Path(ita.__file__).read_text(encoding="utf-8")


def _row(**kw):
    base = dict(deleted=False, seed=False, test_account_flag=False, email_class="other", has_password=True,
                pw_policy_impossible_test=False, pw_policy_compliant_test=False, legacy_default_location=False,
                google_linked=False, apple_linked=False, push_tokens_real_looking=0, swaps_with_non_test=0,
                messages_with_non_test=0)
    return {**base, **kw}


def test_plain_reader_is_genuine():
    assert ita.classify(_row())[0] == "genuine"


def test_email_pattern_alone_never_makes_an_account_test():
    for cls in ("test_like_pattern", "public_test_account"):
        assert ita.classify(_row(email_class=cls))[0] == "review"
    assert ita.classify(_row(pw_policy_compliant_test=True))[0] == "review"
    assert ita.classify(_row(legacy_default_location=True))[0] == "review"


def test_server_set_proof_makes_an_account_test():
    assert ita.classify(_row(test_account_flag=True))[0] == "test"
    assert ita.classify(_row(pw_policy_impossible_test=True, email_class="public_test_account"))[0] == "test"


def test_proof_with_genuine_signals_goes_to_review():
    for extra in (dict(google_linked=True), dict(apple_linked=True), dict(push_tokens_real_looking=1),
                  dict(swaps_with_non_test=2), dict(messages_with_non_test=5)):
        cls, reasons = ita.classify(_row(test_account_flag=True, **extra))
        assert cls == "review" and any(r.startswith("BUT ") for r in reasons), extra


def test_demo_and_tombstones():
    assert ita.classify(_row(seed=True, email_class="demo_domain", has_password=False))[0] == "demo"
    assert ita.classify(_row(seed=True, email_class="demo_domain", has_password=True))[0] == "genuine"
    assert ita.classify(_row(deleted=True, test_account_flag=True))[0] == "tombstone"


def test_email_classes_and_password_checks():
    assert ita.email_class("Test@BookLoop.com") == "public_test_account"
    assert ita.email_class("test_sec3a_1234abcd@bookloop.com") == "test_like_pattern"
    assert ita.email_class("maria.rossi@demo.bookloop") == "demo_domain"
    assert ita.email_class("reader@gmail.com") == "other"
    h = bcrypt.hashpw(b"test1234", bcrypt.gensalt(4)).decode()
    assert ita.password_matches(h, ita.POLICY_IMPOSSIBLE_TEST_PASSWORDS)
    assert not ita.password_matches(bcrypt.hashpw(b"Real!Pass9", bcrypt.gensalt(4)).decode(),
                                    ita.POLICY_IMPOSSIBLE_TEST_PASSWORDS)
    assert not ita.password_matches(None, ita.POLICY_IMPOSSIBLE_TEST_PASSWORDS)


def test_policy_impossible_password_really_fails_the_server_policy():
    # The whole "test1234 => not an APK registration" argument rests on this.
    pw = ita.POLICY_IMPOSSIBLE_TEST_PASSWORDS[0]
    assert not re.search(r"[A-Z]", pw) and not re.search(r"[^A-Za-z0-9]", pw)


def test_push_token_heuristic():
    assert not ita._real_looking_push("ExponentPushToken[" + "t" * 22 + "]")
    assert not ita._real_looking_push("ExponentPushToken[0123456789abcdef012345]")
    assert ita._real_looking_push("ExponentPushToken[xXyY12AbCdEfGhIjKlMn34]")


def test_script_contains_no_write_operations():
    writes = re.findall(r"\.(insert_\w+|update_\w+|replace_one|delete_\w+|drop\w*|find_one_and_\w+|bulk_write|"
                        r"create_index|rename)\(", SCRIPT)
    assert writes == []


def test_output_never_includes_personal_fields():
    fields = re.search(r"fields = \[(.*?)\]", SCRIPT, re.S).group(1)
    for personal in ('"email"', '"name"', "password_hash", "session_token", '"text"'):
        assert personal not in fields


def _status(*action_sets):
    return {"authInfo": {"authenticatedUsers": [{"user": "u", "db": "admin"}],
                         "authenticatedUserPrivileges": [{"resource": {"db": "bookloop", "collection": ""},
                                                          "actions": list(a)} for a in action_sets]}}


def test_read_only_privileges_pass_and_any_write_privilege_is_reported():
    read_role = ["changeStream", "collStats", "dbHash", "dbStats", "find", "killCursors", "listCollections",
                 "listIndexes", "listSearchIndexes", "planCacheRead", "performRawDataOperations"]  # MongoDB 8.x
    assert ita.write_privileges(_status(read_role)) == []
    assert ita.write_privileges(_status(read_role, ["insert", "update", "remove"])) == ["insert", "remove", "update"]
    assert ita.write_privileges(_status(["find", "dropCollection"])) == ["dropCollection"]
