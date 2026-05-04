import discord
import random
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from openai import OpenAI

from config import (
    OPENROUTER_API_KEY, ANALYSIS_PROMPT, GACHA_COST, RARITY_COLORS,
    STREAK_MULTIPLIERS, DAILY_BASE_COINS, SHOP_ROTATE_INTERVAL
)
from db import load_db, save_db, get_member_data
from shop import load_shop, ROLE_COLORS_HEX
from utils import get_channel_by_name, log_action

ai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

# ============================================================
# XP & NIVEAUX
# ============================================================
def xp_for_level(level):
    return int(100 * (1.1 ** level))

def get_level_from_xp(xp):
    level = 0
    total = 0
    while True:
        needed = xp_for_level(level)
        if total + needed > xp:
            return level, xp - total, needed
        total += needed
        level += 1

async def add_xp_and_coins(member, guild, xp_gain, coin_gain):
    db = load_db()
    data = get_member_data(db, member.id)
    old_level = data["level"]
    data["xp"] += xp_gain
    data["coins"] += coin_gain
    new_level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    data["level"] = new_level
    save_db(db)
    if new_level > old_level:
        try:
            embed = discord.Embed(
                title="🎉 Level Up !",
                description=f"Félicitations ! Tu es maintenant **niveau {new_level}** sur **{guild.name}** !",
                color=0xf1c40f
            )
            embed.add_field(name="📈 Nouveau niveau", value=f"**{new_level}**", inline=True)
            embed.add_field(name="✨ XP gagné", value=f"+{xp_gain} XP", inline=True)
            embed.add_field(name="🪙 Pièces gagnées", value=f"+{coin_gain} 🪙", inline=True)
            embed.add_field(name="✨ XP total", value=str(data["xp"]), inline=True)
            embed.add_field(name="🪙 Solde total", value=str(data["coins"]), inline=True)
            embed.add_field(name="📊 Prochain niveau", value=f"{xp_for_level(new_level)} XP requis", inline=True)
            embed.set_footer(text="Continue comme ça ! 💪")
            await member.send(embed=embed)
        except:
            pass

# ============================================================
# ANALYSE IA
# ============================================================
async def analyze_member_messages(guild, member):
    messages = []
    public_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.default_role).read_messages]
    for channel in public_channels:
        try:
            async for msg in channel.history(limit=200):
                if msg.author.id == member.id:
                    messages.append(msg)
                if len(messages) >= 50:
                    break
        except:
            continue
        if len(messages) >= 50:
            break
    activity_days = defaultdict(int)
    for msg in messages:
        day = msg.created_at.strftime("%Y-%m-%d")
        activity_days[day] += 1
    if activity_days:
        avg = round(sum(activity_days.values()) / len(activity_days), 1)
        last = max(activity_days.keys())
        days_since = (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
        if days_since == 0:
            status = "🟢 Actif aujourd'hui"
        elif days_since <= 3:
            status = f"🟡 Actif il y a {days_since} jours"
        elif days_since <= 7:
            status = f"🟠 Peu actif ({days_since} jours)"
        else:
            status = f"🔴 Inactif ({days_since} jours)"
    else:
        avg, status = 0, "⚫ Aucune activité détectée"
    ai_analysis = "Aucun message à analyser."
    if messages:
        msgs_text = "\n".join([f"- {m.content}" for m in messages[:50] if m.content])
        try:
            r = ai_client.chat.completions.create(
                model="openrouter/free",
                messages=[{"role": "user", "content": f"{ANALYSIS_PROMPT}\n\nMessages :\n{msgs_text}"}]
            )
            ai_analysis = r.choices[0].message.content.strip()
        except:
            ai_analysis = "Analyse indisponible."
    return {"status": status, "avg": avg, "total": len(messages), "ai": ai_analysis}

# ============================================================
# UTILITAIRE : équiper un rôle Discord
# ============================================================
async def equip_role_discord(guild, member, item, db, data):
    """Attribue le rôle Discord correspondant à l'item et retire l'ancien rôle couleur."""
    color_hex = ROLE_COLORS_HEX.get(item["id"], 0x95a5a6)
    role_name = item["name"]

    # Retire les anciens rôles couleur équipés
    old_equipped = data.get("equipped", [])
    for old_name in old_equipped:
        old_role = discord.utils.get(guild.roles, name=old_name)
        if old_role and old_role in member.roles:
            try:
                await member.remove_roles(old_role)
            except:
                pass

    # Cherche ou crée le rôle Discord
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        try:
            role = await guild.create_role(
                name=role_name,
                color=discord.Color(color_hex),
                reason="Rôle boutique créé automatiquement par Prowler"
            )
        except Exception as e:
            return False, f"Impossible de créer le rôle : {e}"

    # Attribue le rôle
    try:
        await member.add_roles(role)
    except Exception as e:
        return False, f"Impossible d'attribuer le rôle : {e}"

    data["equipped"] = [role_name]
    return True, role_name

# ============================================================
# COMMANDES UTILISATEURS
# ============================================================
async def cmd_profil(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    progress = int((current_xp / needed_xp) * 10) if needed_xp > 0 else 0
    progress_bar = "█" * progress + "░" * (10 - progress)
    embed = discord.Embed(title=f"👤 Profil — {message.author.display_name}", color=0x3498db)
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="⭐ Niveau", value=str(level), inline=True)
    embed.add_field(name="✨ XP", value=f"{current_xp}/{needed_xp}", inline=True)
    embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
    embed.add_field(name="📊 Progression", value=f"`{progress_bar}`", inline=False)
    embed.add_field(name="🔥 Streak daily", value=f"{data['daily_streak']} jours", inline=True)
    equipped = data.get("equipped", [])
    embed.add_field(name="👗 Rôle équipé", value=", ".join(equipped) if equipped else "Aucun", inline=True)
    await message.channel.send(embed=embed)

# ============================================================
# INVENTAIRE AVEC RÉACTIONS
# ============================================================

# Emojis numérotés pour les réactions (max 10 items affichés)
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

async def cmd_inventaire(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    inventory = data.get("inventory", [])
    equipped = data.get("equipped", [])

    embed = discord.Embed(
        title=f"🎒 Inventaire — {message.author.display_name}",
        color=0x9b59b6
    )

    if not inventory:
        embed.description = "Tu n'as aucun article dans ton inventaire."
        await message.channel.send(embed=embed)
        return

    # Limite à 10 items pour les réactions
    display_items = inventory[:10]
    lines = []
    for i, item in enumerate(display_items):
        emoji = NUMBER_EMOJIS[i]
        is_equipped = item["name"] in equipped
        equipped_tag = " ✅" if is_equipped else ""
        expire_tag = f" — expire le {item['expires']}" if item.get("expires") else ""
        lines.append(f"{emoji} **{item['name']}**{equipped_tag}{expire_tag}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Réagis avec le numéro pour équiper/déséquiper • ✅ = rôle actuellement équipé")

    inv_msg = await message.channel.send(embed=embed)

    # Ajoute les réactions
    for i in range(len(display_items)):
        try:
            await inv_msg.add_reaction(NUMBER_EMOJIS[i])
        except:
            break

    # Attend une réaction de l'auteur (30 secondes)
    def check(reaction, user):
        return (
            user.id == message.author.id
            and reaction.message.id == inv_msg.id
            and str(reaction.emoji) in NUMBER_EMOJIS[:len(display_items)]
        )

    try:
        reaction, user = await message.channel._state._get_client().wait_for(
            "reaction_add", timeout=30.0, check=check
        )
        idx = NUMBER_EMOJIS.index(str(reaction.emoji))
        item = display_items[idx]

        db = load_db()
        data = get_member_data(db, message.author.id)
        equipped = data.get("equipped", [])

        # ✅ TOGGLE : si déjà équipé → déséquiper
        if item["name"] in equipped:
            role = discord.utils.get(message.guild.roles, name=item["name"])
            if role and role in message.author.roles:
                try:
                    await message.author.remove_roles(role)
                except:
                    pass
            data["equipped"] = []
            save_db(db)
            confirm_embed = discord.Embed(
                title="👗 Rôle retiré !",
                description=f"Tu ne portes plus **{item['name']}**.",
                color=0xe74c3c
            )
            await message.channel.send(embed=confirm_embed)
            await log_action(message.guild, "shop_equip", None, message.author, extra={"Rôle retiré": item["name"]})

        # Sinon → équiper normalement
        else:
            success, result = await equip_role_discord(message.guild, message.author, item, db, data)
            save_db(db)
            if success:
                confirm_embed = discord.Embed(
                    title="👗 Rôle équipé !",
                    description=f"Tu portes maintenant **{result}** !",
                    color=0x2ecc71
                )
                await message.channel.send(embed=confirm_embed)
                await log_action(message.guild, "shop_equip", None, message.author, extra={"Rôle équipé": result})
            else:
                await message.channel.send(f"❌ {result}")

    except Exception:
        # Timeout ou autre erreur — on supprime les réactions silencieusement
        try:
            await inv_msg.clear_reactions()
        except:
            pass

async def cmd_boutique(message):
    shop = load_shop()
    embed = discord.Embed(title="🛍️ Boutique", color=0x2ecc71)
    standard_text = "\n".join([f"• **{i['name']}** — {i['price']} 🪙" for i in shop["standard"]])
    embed.add_field(name="📦 Articles permanents", value=standard_text or "Aucun", inline=False)
    if shop["rotating"]:
        last = shop.get("last_rotate")
        if last:
            dt = datetime.fromisoformat(last)
            next_rotate = dt + timedelta(seconds=SHOP_ROTATE_INTERVAL)
            remaining = next_rotate - datetime.now(timezone.utc)
            mins = int(remaining.total_seconds() // 60)
            rotate_txt = f"Se renouvelle dans **{mins // 60}h{mins % 60}min**"
        else:
            rotate_txt = ""
        rotating_text = "\n".join([f"• **{i['name']}** — {i['price']} 🪙" for i in shop["rotating"]])
        embed.add_field(name=f"🔄 Boutique rotative — {rotate_txt}", value=rotating_text, inline=False)
    embed.set_footer(text="!acheter [nom] pour acheter • !spin pour le gacha (50 🪙)")
    await message.channel.send(embed=embed)
    gacha_items = shop.get("gacha", [])
    if gacha_items:
        rarity_weight = {"légendaire": 2, "épique": 8, "rare": 20, "commun": 70}
        total_w = sum(rarity_weight.get(i.get("rarity", "commun"), 70) for i in gacha_items)
        rarity_labels = {"légendaire": "🌟 Légendaire", "épique": "💜 Épique", "rare": "💙 Rare", "commun": "⬜ Commun"}
        gacha_embed = discord.Embed(title="🎰 Gacha — Rôles disponibles", description=f"Prix : **{GACHA_COST}** 🪙 par spin\nUtilise `!spin` pour tenter ta chance !", color=0xf1c40f)
        for item in gacha_items:
            rarity = item.get("rarity", "commun")
            w = rarity_weight.get(rarity, 70)
            pct = round((w / total_w) * 100, 2) if total_w > 0 else 0
            color_hex = ROLE_COLORS_HEX.get(item["id"], 0x95a5a6)
            r = (color_hex >> 16) & 0xFF
            g = (color_hex >> 8) & 0xFF
            b = color_hex & 0xFF
            color_square = "🟥" if r > 200 and g < 100 else "🟦" if b > 200 and r < 100 else "🟩" if g > 200 and r < 100 else "🟨" if r > 200 and g > 200 else "🟪" if b > 150 and r > 100 else "⬜"
            gacha_embed.add_field(
                name=f"{color_square} {item['name']}",
                value=f"{rarity_labels.get(rarity, rarity)}\n**{pct}%** de chance",
                inline=True
            )
        await message.channel.send(embed=gacha_embed)

async def cmd_acheter(message, item_name):
    if not item_name:
        await message.channel.send("❌ Usage : `!acheter [nom de l'article]`")
        return
    shop = load_shop()
    all_items = shop["standard"] + shop["rotating"]
    item = next((i for i in all_items if i["name"].lower() == item_name.lower()), None)
    if not item:
        await message.channel.send(f"❌ Article **{item_name}** introuvable dans la boutique.")
        return
    db = load_db()
    data = get_member_data(db, message.author.id)
    if data["coins"] < item["price"]:
        await message.channel.send(f"❌ Tu n'as pas assez de pièces. (Tu as **{data['coins']}** 🪙, il faut **{item['price']}** 🪙)")
        return
    if item.get("duration") is None:
        already = any(i["id"] == item["id"] for i in data["inventory"])
        if already:
            await message.channel.send(f"❌ Tu possèdes déjà **{item['name']}**.")
            return
    data["coins"] -= item["price"]
    inv_item = {"id": item["id"], "name": item["name"], "type": item["type"]}
    if item.get("duration"):
        expires = (datetime.now(timezone.utc) + timedelta(days=item["duration"])).strftime("%d/%m/%Y")
        inv_item["expires"] = expires
    data["inventory"].append(inv_item)
    save_db(db)
    embed = discord.Embed(
        title="✅ Achat réussi !",
        description=f"Tu as acheté **{item['name']}** pour **{item['price']}** 🪙\nSolde restant : **{data['coins']}** 🪙",
        color=0x2ecc71
    )
    await message.channel.send(embed=embed)
    await log_action(message.guild, "shop_buy", None, message.author, extra={"Article": item["name"], "Prix": f"{item['price']} 🪙"})

async def cmd_equiper(message, item_name):
    """Équipe un rôle depuis la commande texte !équiper"""
    if not item_name:
        await message.channel.send("❌ Usage : `!équiper [nom du rôle]`")
        return

    db = load_db()
    data = get_member_data(db, message.author.id)
    inventory = data.get("inventory", [])

    # Recherche flexible : ignore la casse ET les emojis en début de nom
    def normalize(s):
        # Retire les emojis Unicode courants en début de chaîne
        import re
        return re.sub(r'^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\U0001F300-\U0001F9FF\s]+', '', s).strip().lower()

    item = next(
        (i for i in inventory if normalize(i["name"]) == normalize(item_name)),
        None
    )

    # Fallback : recherche partielle si aucun match exact
    if not item:
        item = next(
            (i for i in inventory if normalize(item_name) in normalize(i["name"])),
            None
        )

    if not item:
        await message.channel.send(f"❌ Tu ne possèdes pas **{item_name}**. Achète-le d'abord !")
        return

    success, result = await equip_role_discord(message.guild, message.author, item, db, data)
    save_db(db)

    if success:
        embed = discord.Embed(
            title="👗 Rôle équipé !",
            description=f"Tu portes maintenant **{result}** !",
            color=0x3498db
        )
        await message.channel.send(embed=embed)
        await log_action(message.guild, "shop_equip", None, message.author, extra={"Rôle équipé": result})
    else:
        await message.channel.send(f"❌ {result}")

async def cmd_spin(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    if data["coins"] < GACHA_COST:
        await message.channel.send(f"❌ Tu n'as pas assez de pièces. (Tu as **{data['coins']}** 🪙, il faut **{GACHA_COST}** 🪙)")
        return
    shop = load_shop()
    gacha_pool = shop["gacha"]
    if not gacha_pool:
        await message.channel.send("❌ Le gacha est vide pour l'instant.")
        return

    weights = []
    for item in gacha_pool:
        r = item.get("rarity", "commun")
        weights.append({"légendaire": 2, "épique": 8, "rare": 20}.get(r, 70))
    won_item = random.choices(gacha_pool, weights=weights, k=1)[0]

    data["coins"] -= GACHA_COST
    save_db(db)

    # ── Animation suspense ──────────────────────────────────────
    suspense_frames = [
        ("🎰 Spin en cours...", "❓ ❓ ❓"),
        ("🎰 Ça tourne...", "⬛ ❓ ❓"),
        ("🎰 Presque...", "⬛ ⬛ ❓"),
        ("🎰 C'est...", "⬛ ⬛ 🎁"),
    ]

    import asyncio
    embed_anim = discord.Embed(title="🎰 Spin en cours...", description="❓ ❓ ❓", color=0xf1c40f)
    spin_msg = await message.channel.send(embed=embed_anim)

    for title, desc in suspense_frames[1:]:
        await asyncio.sleep(0.9)
        embed_anim = discord.Embed(title=title, description=desc, color=0xf1c40f)
        await spin_msg.edit(embed=embed_anim)

    await asyncio.sleep(0.9)
    # ── Résultat final ──────────────────────────────────────────

    db = load_db()
    data = get_member_data(db, message.author.id)
    already = any(i["id"] == won_item["id"] for i in data["inventory"])
    if not already:
        data["inventory"].append({"id": won_item["id"], "name": won_item["name"], "type": won_item["type"]})
        result_txt = f"Tu as obtenu **{won_item['name']}** !"
    else:
        refund = 10
        data["coins"] += refund
        result_txt = f"Tu as obtenu **{won_item['name']}** (déjà possédé → **+{refund}** 🪙 remboursés)"
    save_db(db)

    rarity = won_item.get("rarity", "commun")
    color = RARITY_COLORS.get(rarity, 0x95a5a6)
    rarity_weight = {"légendaire": 2, "épique": 8, "rare": 20, "commun": 70}
    total_weight = sum(rarity_weight.get(i.get("rarity", "commun"), 70) for i in gacha_pool)
    item_weight = rarity_weight.get(rarity, 70)
    chance_pct = round((item_weight / total_weight) * 100, 2) if total_weight > 0 else 0
    rarity_labels = {"légendaire": "🌟 Légendaire", "épique": "💜 Épique", "rare": "💙 Rare", "commun": "⬜ Commun"}

    embed_result = discord.Embed(title="✨ Résultat du Gacha !", color=color)
    embed_result.add_field(name="🎁 Récompense", value=result_txt, inline=False)
    embed_result.add_field(name="✨ Rareté", value=rarity_labels.get(rarity, rarity), inline=True)
    embed_result.add_field(name="🎯 Probabilité", value=f"**{chance_pct}%** de chance", inline=True)
    embed_result.add_field(name="🪙 Solde", value=str(data["coins"]), inline=True)
    embed_result.set_footer(text=f"Coût : {GACHA_COST} 🪙 • Légendaire : {round(2/total_weight*100,2)}% • Épique : {round(8/total_weight*100,2)}% • Rare : {round(20/total_weight*100,2)}% • Commun : {round(70/total_weight*100,2)}%")

    await spin_msg.edit(embed=embed_result)
    await log_action(message.guild, "gacha", None, message.author, extra={"Obtenu": won_item["name"], "Rareté": rarity})

async def cmd_classement(message):
    db = load_db()
    members_data = []
    for mid, data in db.items():
        member = message.guild.get_member(int(mid))
        if member:
            level, _, _ = get_level_from_xp(data.get("xp", 0))
            members_data.append((member.display_name, level, data.get("xp", 0), data.get("coins", 0)))
    members_data.sort(key=lambda x: x[2], reverse=True)
    top = members_data[:10]
    embed = discord.Embed(title="🏆 Classement — Top 10", color=0xf1c40f)
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [f"{medals[i]} **{name}** — Niv. {level} • {xp} XP • {coins} 🪙" for i, (name, level, xp, coins) in enumerate(top)]
    embed.description = "\n".join(lines) if lines else "Aucun membre classé."
    await message.channel.send(embed=embed)

async def cmd_daily(message):
    channel_name = message.channel.name.lower().replace("・", "")
    if "daily" not in channel_name:
        daily_ch = get_channel_by_name(message.guild, "daily")
        if daily_ch:
            await message.channel.send(f"❌ La commande `!daily` est réservée à {daily_ch.mention} !")
        return
    db = load_db()
    data = get_member_data(db, message.author.id)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    last = data.get("last_daily")
    if last == today:
        await message.channel.send("⏳ Tu as déjà récupéré ta récompense aujourd'hui ! Reviens demain.")
        return
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    data["daily_streak"] = (data["daily_streak"] + 1) if last == yesterday else 1
    streak = data["daily_streak"]
    multiplier = 1.0
    for days, mult in sorted(STREAK_MULTIPLIERS.items()):
        if streak >= days:
            multiplier = mult
    coins_earned = int(DAILY_BASE_COINS * multiplier)
    xp_earned = int(20 * multiplier)
    data["coins"] += coins_earned
    data["xp"] += xp_earned
    data["last_daily"] = today
    save_db(db)
    embed = discord.Embed(title="🎁 Récompense quotidienne !", color=0xf39c12)
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="🪙 Pièces gagnées", value=str(coins_earned), inline=True)
    embed.add_field(name="✨ XP gagnés", value=str(xp_earned), inline=True)
    embed.add_field(name="🔥 Streak", value=f"{streak} jours", inline=True)
    if multiplier > 1.0:
        embed.add_field(name="⚡ Bonus streak", value=f"x{multiplier}", inline=True)
    embed.add_field(name="🪙 Solde total", value=str(data["coins"]), inline=True)
    embed.set_footer(text="Reviens demain pour continuer ton streak !")
    await message.channel.send(embed=embed)
    await log_action(message.guild, "daily", None, message.author, extra={"Pièces": coins_earned, "Streak": streak})

async def cmd_parrainer(message, args):
    mentions = message.mentions
    if not mentions:
        await message.channel.send("❌ Usage : `!parrainer @pseudo`")
        return
    target = mentions[0]
    if target.id == message.author.id:
        await message.channel.send("❌ Tu ne peux pas te parrainer toi-même !")
        return
    db = load_db()
    data_author = get_member_data(db, message.author.id)
    data_target = get_member_data(db, target.id)
    if data_target.get("godfather"):
        await message.channel.send(f"❌ **{target.display_name}** a déjà un parrain.")
        return
    bonus = 100
    data_author["coins"] += bonus
    data_target["coins"] += bonus
    data_target["godfather"] = str(message.author.id)
    save_db(db)
    embed = discord.Embed(
        title="🤝 Parrainage réussi !",
        description=f"**{message.author.display_name}** a parrainé **{target.display_name}** !\nVous recevez chacun **{bonus}** 🪙",
        color=0x2ecc71
    )
    await message.channel.send(embed=embed)
