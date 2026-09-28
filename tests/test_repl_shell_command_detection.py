from app.repl import SHELL_COMMAND_PATTERN


def test_matches_common_package_manager_install_commands():
    # Regression case from a real session: "pip install ddgs" (no leading "!")
    # was falling through to Jev, which routed it to the WebSearch agent and
    # got back an answer about a completely unrelated prior question.
    assert SHELL_COMMAND_PATTERN.match("pip install ddgs")
    assert SHELL_COMMAND_PATTERN.match("pip3 install requests")
    assert SHELL_COMMAND_PATTERN.match("npm install express")
    assert SHELL_COMMAND_PATTERN.match("brew install node")
    assert SHELL_COMMAND_PATTERN.match("apt-get install curl")
    assert SHELL_COMMAND_PATTERN.match("docker run alpine")


def test_does_not_match_natural_language_that_shares_a_leading_word():
    assert not SHELL_COMMAND_PATTERN.match("go check the readme")
    assert not SHELL_COMMAND_PATTERN.match("run the tests please")
    assert not SHELL_COMMAND_PATTERN.match("what is the latest model")
    assert not SHELL_COMMAND_PATTERN.match("install this feature for me")
    assert not SHELL_COMMAND_PATTERN.match("python3 script.py")
