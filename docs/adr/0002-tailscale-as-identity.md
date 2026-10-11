# Tailscale is the network and identity layer

HomeBase listens only on the tailnet and identifies each calling Machine with `tailscale whois`. Every registered Machine is bound to a Tailscale device, so there are no tokens or login system. We accept that every Machine must join the tailnet in exchange for getting authentication, reachability and, later, the ability for HomeBase to dial a Machine directly for push Handoffs, all without building them ourselves.
