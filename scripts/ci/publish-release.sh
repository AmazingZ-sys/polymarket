#!/usr/bin/env bash
set -euo pipefail
: "${VERSION:?}" "${IMAGE_PREFIX:?}" "${GITHUB_SHA:?}" "${GITHUB_REPOSITORY:?}" "${GH_TOKEN:?}"
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $GITHUB_SHA =~ ^[a-f0-9]{40}$ ]]
tag="v$VERSION"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
asset=polyquant-deploy.tar.gz
bundle="$work/$asset"
python3 scripts/ci/deployment-bundle.py "$VERSION" "$IMAGE_PREFIX" "$bundle"
if git show-ref --verify --quiet "refs/tags/$tag"; then
  if [[ $(git rev-list -1 "$tag") != "$GITHUB_SHA" ]]; then
    echo 'Version already belongs to another commit' >&2
    exit 1
  fi
fi

notes="$work/notes"
printf 'Source commit: `%s`\n' "$GITHUB_SHA" > "$notes"
echo >> "$notes"
for component in api web worker; do
  image="$IMAGE_PREFIX-$component"
  staging="$image:sha-$GITHUB_SHA"
  digest=$(docker buildx imagetools inspect "$staging" --format '{{.Manifest.Digest}}')
  [[ $digest =~ ^sha256:[a-f0-9]{64}$ ]]
  existing=$(docker buildx imagetools inspect "$image:$VERSION" --format '{{.Manifest.Digest}}' 2>/dev/null || true)
  if [[ -n $existing && $existing != "$digest" ]]; then
    echo "$image:$VERSION already points to another manifest" >&2
    exit 1
  fi
  if [[ -z $existing ]]; then
    docker buildx imagetools create --tag "$image:$VERSION" "$image@$digest"
  fi
  [[ $(docker buildx imagetools inspect "$image:$VERSION" --format '{{.Manifest.Digest}}') == "$digest" ]]
  printf -- '- `%s:%s` · `%s`\n' "$image" "$VERSION" "$digest" >> "$notes"
done
if release=$(gh release view "$tag" --repo "$GITHUB_REPOSITORY" --json isDraft,targetCommitish,assets); then
  draft=$(jq -r .isDraft <<<"$release")
  if [[ $draft == true ]]; then
    [[ $(jq -r .targetCommitish <<<"$release") == "$GITHUB_SHA" ]]
  else
    [[ $(git rev-list -1 "$tag") == "$GITHUB_SHA" ]]
  fi
  if jq -e --arg asset "$asset" '.assets[] | select(.name == $asset and .state == "uploaded")' <<<"$release" >/dev/null; then
    mkdir "$work/existing"
    gh release download "$tag" --repo "$GITHUB_REPOSITORY" --pattern "$asset" --dir "$work/existing"
    cmp "$bundle" "$work/existing/$asset"
  elif [[ $draft == true ]]; then
    gh release upload "$tag" "$bundle" --repo "$GITHUB_REPOSITORY" --clobber
  else
    gh release upload "$tag" "$bundle" --repo "$GITHUB_REPOSITORY"
  fi
  if [[ $draft == true ]]; then gh release edit "$tag" --repo "$GITHUB_REPOSITORY" --draft=false; fi
  exit 0
fi
gh release create "$tag" "$bundle" --repo "$GITHUB_REPOSITORY" --target "$GITHUB_SHA" \
  --generate-notes --notes-file "$notes" --title "PolyQuant Pro $VERSION"
