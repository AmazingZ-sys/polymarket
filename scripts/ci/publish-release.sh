#!/usr/bin/env bash
set -euo pipefail
: "${VERSION:?}" "${IMAGE_PREFIX:?}" "${GITHUB_SHA:?}" "${GITHUB_REPOSITORY:?}" "${GH_TOKEN:?}"
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $GITHUB_SHA =~ ^[a-f0-9]{40}$ ]]
tag="v$VERSION"
if git show-ref --verify --quiet "refs/tags/$tag"; then
  if [[ $(git rev-list -1 "$tag") != "$GITHUB_SHA" ]]; then
    echo 'Version already belongs to another commit' >&2
    exit 1
  fi
  if gh release view "$tag" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
    echo "Release $tag already exists at this commit"
    exit 0
  fi
fi

notes=$(mktemp)
trap 'rm -f "$notes"' EXIT
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
gh release create "$tag" --repo "$GITHUB_REPOSITORY" --target "$GITHUB_SHA" \
  --generate-notes --notes-file "$notes" --title "PolyQuant Pro $VERSION"
