from ateam.claude_code import encode_project_dir, rewrite


def test_encode_project_dir_matches_claude_code():
    assert encode_project_dir("/Users/jjmachan/workspace/personal/ateam/main") == "-Users-jjmachan-workspace-personal-ateam-main"
    assert encode_project_dir("/a/main.feat-x") == "-a-main-feat-x"


def test_rewrite_whole_prefixes_only_and_in_one_pass():
    data = b'{"cwd":"/Users/j/ws/p/main","x":"/Users/j/ws2/other","y":"cd /Users/j/ws && ls","z":"\\"/Users/j/ws\\""}'
    out = rewrite(data, [("/Users/j/ws", "/home/j/ws"), ("/home/j/ws", "/never")])
    assert out == b'{"cwd":"/home/j/ws/p/main","x":"/Users/j/ws2/other","y":"cd /home/j/ws && ls","z":"\\"/home/j/ws\\""}'


def test_longest_prefix_wins():
    out = rewrite(b'"/r/p/feat-x/src"', [("/r", "/R"), ("/r/p/feat-x", "/R/p/main.feat-x")])
    assert out == b'"/R/p/main.feat-x/src"'
