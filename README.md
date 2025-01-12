## Setup

git remote add samsungtvws https://github.com/NickWaterton/samsung-tv-ws-api.git
git fetch samsungtvws
git checkout -b upstream/samsungtvws samsungtvws/NickWaterton-patch-1
git subtree split -q --squash --prefix samsungtvws --annotate="[samsungtvws] " --rejoin -b merge/samsungtvws
git checkout master
git subtree add --prefix=samsungtvws --squash merge/samsungtvws
git branch -D upstream/samsungtvws merge/samsungtvws
git remote remove samsungtvws

## Update

git remote add samsungtvws https://github.com/NickWaterton/samsung-tv-ws-api.git
git checkout -b upstream/samsungtvws samsungtvws/master
git fetch samsungtvws
git checkout -b upstream/samsungtvws samsungtvws/master
git subtree split -q --prefix samsungtvws --annotate="[samsungtvws] " --rejoin -b merge/samsungtvws
git checkout master
git subtree merge -q --prefix samsungtvws --squash merge/samsungtvws -m "updating samsungtvws"
git branch -D upstream/samsungtvws merge/samsungtvws
git remote remove samsungtvws


