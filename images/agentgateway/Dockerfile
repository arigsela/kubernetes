# syntax=docker/dockerfile:1
# agentgateway as the MCP adapter image (§T.133): one static (musl) release binary at
# /app/agentgateway, the path upstream's own images used. Build it with
# scripts/build-agentgateway-image.sh, which downloads the binary at a pinned version, checks
# its pinned sha256, and stages it as the build context.
#
# Only used as the init container of base-apps/kagent/{agent-docs,brave-search,implementer}-mcp:
# `--copy-self /adapterbin/agentgateway` copies the binary (argv[0]) into an emptyDir, and the
# MCP server's own container runs it from there with the stdio server as its child.
# --copy-self is compiled into musl builds only, and upstream stopped publishing musl images
# after v1.3.1 (agentgateway#2574). Being static, the copy runs in any image: the Debian-based
# github-mcp-server and the Alpine-based mcp/brave-search alike.
#
# Non-root: the copy only needs to write the emptyDir (world-writable), and the copied file
# keeps mode 0755, so the server container's user can run it whatever its uid.
FROM scratch
COPY --chmod=0755 agentgateway /app/agentgateway
USER 65532:65532
ENTRYPOINT ["/app/agentgateway"]
