#!/bin/bash -ex
# Run inside the git-fat Docker image (git + git-fat both on PATH).

rm -rf /tmp/fat-test /tmp/fat-test2 /tmp/fat-store
mkdir -p /tmp/fat-store

git init /tmp/fat-test
cd /tmp/fat-test
git fat init
cat >> .gitfat <<EOF
[rsync]
remote = /tmp/fat-store
EOF
echo '*.fat filter=fat -crlf' > .gitattributes
git add .gitattributes .gitfat
git commit -m'Initial fat repository'

ln -s /nonexistent/broken-symlink-target c
git add c
git commit -m'add broken symlink'
echo 'fat content a' > a.fat
git add a.fat
git commit -m'add a.fat'
echo 'fat content b' > b.fat
git add b.fat
git commit -m'add b.fat'
echo 'revise fat content a' > a.fat
git commit -am'revise a.fat'
git fat push

cd /tmp
git clone /tmp/fat-test /tmp/fat-test2
cd /tmp/fat-test2

if git fat checkout; then
    echo 'ERROR: "git fat checkout" in uninitialised repo should fail'
    exit 1
fi
if git fat pull -- 'a.fa*'; then
    echo 'ERROR: "git fat pull" in uninitialised repo should fail'
    exit 1
fi

git fat init
git fat pull -- 'a.fa*'
grep -q 'revise fat content a' a.fat

echo 'file which is committed and removed afterwards' > d
git add d
git commit -m'add d with normal content'
rm d
git fat pull

mv .git/fat/objects/6ecec2e21d3033e7ba53e2db63f69dbd3a011fa8 \
   .git/fat/objects/6ecec2e21d3033e7ba53e2db63f69dbd3a011fa8.bak
echo "Not the right data" > .git/fat/objects/6ecec2e21d3033e7ba53e2db63f69dbd3a011fa8
if git fat verify; then
    echo "Verify did not detect invalid object"
    exit 1
fi
mv .git/fat/objects/6ecec2e21d3033e7ba53e2db63f69dbd3a011fa8.bak \
   .git/fat/objects/6ecec2e21d3033e7ba53e2db63f69dbd3a011fa8

echo "All in-container tests passed."
