# Builds the all-in-one image from this checkout: the three images it is
# made of first, then the image itself from their files.
#
#   docker buildx bake -f docker-bake.hcl all-in-one
#   VERSION=1.5.0 docker buildx bake -f docker-bake.hcl all-in-one
#
# Name the file: without -f, bake also reads compose.yaml and stops on its
# unset HIKARI_VIDEO.
#
# The other images build with plain `docker build` (docs/BUILDING.md).

variable "VERSION" {
  default = "local"
}

# Filled in by the release workflow (tags and labels); empty here.
target "docker-metadata-action" {}

target "gallery" {
  context = "gallery"
  args = {
    HIKARI_GALLERY_VERSION = "gallery-${VERSION}"
  }
}

target "worker" {
  context    = "."
  dockerfile = "containers/worker/Dockerfile"
  args = {
    HIKARI_WORKER_VERSION = "worker-${VERSION}"
  }
}

target "text-encoder" {
  context    = "."
  dockerfile = "containers/text-encoder/Dockerfile"
}

target "all-in-one" {
  inherits   = ["docker-metadata-action"]
  context    = "."
  dockerfile = "containers/all-in-one/Dockerfile"
  contexts = {
    gallery      = "target:gallery"
    worker       = "target:worker"
    text-encoder = "target:text-encoder"
  }
  args = {
    HIKARI_GALLERY_VERSION = "gallery-${VERSION}"
  }
  tags = ["hikari-index/hikari-index:${VERSION}"]
}

group "default" {
  targets = ["all-in-one"]
}
