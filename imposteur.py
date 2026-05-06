import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import json
import os
from datetime import datetime, timezone

DB_IMPOSTEUR = "/tmp/imposteur.json"

def load_imp_db():
    if os.path.exists(DB_IMPOSTEUR):
        with open(DB_IMPOSTEUR, "r") as f:
            return json.load(f)
    return {}

def save_imp_db(db):
    os.makedirs(os.path.dirname(DB_IMPOSTEUR), exist_ok=True)
    with open(DB_IMPOSTEUR, "w") as f:
        json.dump(db, f, indent=2)

MOTS_PAIRES = [
    ("Ferrari", "Lamborghini"), ("Pizza", "Burger"), ("Chat", "Chien"),
    ("Minecraft", "Roblox"), ("Paris", "Lyon"), ("Basketball", "Football"),
    ("iPhone", "Samsung"), ("Coca", "Pepsi"), ("Été", "Hiver"),
    ("Dragon", "Licorne"), ("Vampire", "Loup-garou"), ("Sushi", "Tacos"),
]


# ============================================================
# VUE CONFIRMATION AVANT LANCEMENT
# ============================================================
class ConfirmLancementView(discord.ui.View):
    def __init__(self, cog, salon, createur, joueurs, manches, debat):
        super().__init__(timeout=60)
        self.cog = cog
        self.salon = salon
        self.createur = createur
        self.joueurs = joueurs
        self.manches = manches
        self.debat = debat
        self.confirmed = False

    @discord.ui.button(label="▶️ Lancer la partie", style=discord.ButtonStyle.green)
    async def lancer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.createur:
            await interaction.response.send_message("❌ Seul le créateur peut lancer la partie.", ephemeral=True)
            return
        self.confirmed = True
        self.stop()
        await interaction.response.send_message("🎮 Lancement de la partie !", ephemeral=True)
        await self.cog._lancer_partie(self.salon, self.createur, self.joueurs, self.manches, self.debat)

    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.red)
    async def annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.createur:
            await interaction.response.send_message("❌ Seul le créateur peut annuler.", ephemeral=True)
            return
        self.confirmed = False
        self.stop()
        await interaction.response.send_message("❌ Lancement annulé.", ephemeral=False)
        # Supprime le salon privé créé si annulation
        self.cog.salons_prives.pop(self.createur.id, None)
        try:
            await self.salon.send("❌ Partie annulée. Ce salon sera supprimé dans 10 secondes.")
            await asyncio.sleep(10)
            await self.salon.delete(reason="Partie annulée avant lancement")
        except:
            pass

    async def on_timeout(self):
        if not self.confirmed:
            self.cog.salons_prives.pop(self.createur.id, None)
            try:
                await self.salon.send("⏱️ Délai dépassé — la partie n'a pas été lancée. Salon supprimé dans 10s.")
                await asyncio.sleep(10)
                await self.salon.delete(reason="Timeout confirmation")
            except:
                pass


class Imposteur(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.parties_actives = {}   # salon_id: game_state
        self.salons_prives = {}     # user_id: channel_id (1 max par personne)

    def is_salon_prive_imposteur(self, channel_id):
        """Vérifie si le channel_id est bien un salon privé imposteur géré par ce cog."""
        return channel_id in self.salons_prives.values()

    async def creer_salon_prive(self, guild, createur, joueurs):
        """Crée le salon texte privé pour le jeu."""
        ts = int(datetime.now(timezone.utc).timestamp())
        nom = f"txt-{createur.name}-imposteur-{ts}"[:100]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                manage_channels=True, manage_messages=True
            ),
            createur: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        for j in joueurs:
            overwrites[j] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        try:
            salon = await guild.create_text_channel(
                name=nom, overwrites=overwrites,
                topic=f"Jeu Imposteur — Créé par {createur.display_name}"
            )
            return salon
        except Exception as e:
            print(f"❌ Erreur création salon imposteur : {e}")
            return None

    # ── SETUP ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="imposteur", description="Lance le jeu Imposteur Mots dans un salon privé")
    @app_commands.describe(
        manches="Nombre de manches (1, 3, 5 ou 7)",
        debat="Durée du débat en secondes (0 = pas de débat)"
    )
    @app_commands.choices(
        manches=[app_commands.Choice(name=str(n), value=n) for n in [1, 3, 5, 7]],
        debat=[app_commands.Choice(name=f"{n}s" if n > 0 else "Pas de débat", value=n) for n in [0, 30, 45, 60]]
    )
    async def slash_imposteur(self, interaction: discord.Interaction,
                               manches: int = 3, debat: int = 45):
        await interaction.response.defer(ephemeral=True)
        createur = interaction.user
        guild = interaction.guild

        # Vérif 1 salon max par personne
        if createur.id in self.salons_prives:
            ch_id = self.salons_prives[createur.id]
            ch = guild.get_channel(ch_id)
            if ch:
                await interaction.followup.send(
                    f"❌ Tu as déjà un salon imposteur actif : {ch.mention}\n"
                    f"Utilise `/suppr` pour le fermer d'abord.",
                    ephemeral=True
                )
                return
            else:
                # Le salon n'existe plus, on nettoie
                del self.salons_prives[createur.id]

        # Sélection des joueurs
        embed_sel = discord.Embed(
            title="🎮 Setup Imposteur Mots",
            description=(
                f"**Manches :** {manches} | **Débat :** {debat}s\n\n"
                "Mentionne les joueurs qui participeront (sépare par espace) :\n"
                "Ex: `@Joueur1 @Joueur2 @Joueur3`\n\n"
                "Ou envoie `tous` pour inclure tous les membres en ligne.\n"
                "Ou envoie `annule` pour annuler."
            ),
            color=0x9B59B6
        )
        await interaction.followup.send(embed=embed_sel, ephemeral=False)

        def check(m):
            return m.author == createur and m.channel == interaction.channel

        try:
            rep = await self.bot.wait_for("message", timeout=60, check=check)
        except asyncio.TimeoutError:
            await interaction.channel.send(f"{createur.mention} ⏱️ Délai dépassé, setup annulé.")
            return

        # ✅ FIX 1 — Annulation possible avant lancement
        if rep.content.lower() == "annule":
            try:
                await rep.delete()
            except:
                pass
            await interaction.channel.send(f"{createur.mention} ❌ Setup annulé.")
            return

        if rep.content.lower() == "tous":
            joueurs = [m for m in guild.members if not m.bot and m != createur and m.status != discord.Status.offline][:10]
        else:
            joueurs = rep.mentions

        try:
            await rep.delete()
        except:
            pass

        if len(joueurs) < 2:
            await interaction.channel.send(f"{createur.mention} ❌ Il faut au moins **2 joueurs** !")
            return

        # Crée le salon privé
        salon = await self.creer_salon_prive(guild, createur, joueurs)
        if not salon:
            await interaction.channel.send(f"{createur.mention} ❌ Impossible de créer le salon privé.")
            return

        self.salons_prives[createur.id] = salon.id

        tous_joueurs = [createur] + joueurs

        # ✅ FIX 2 — Embed de confirmation avec boutons Lancer / Annuler
        embed_confirm = discord.Embed(
            title="🎮 Prêt à lancer ?",
            description=(
                f"**Joueurs ({len(tous_joueurs)}) :** {', '.join(j.mention for j in tous_joueurs)}\n"
                f"**Manches :** {manches} | **Débat :** {debat}s\n\n"
                f"Le jeu se déroulera dans {salon.mention}\n\n"
                "Clique **▶️ Lancer** pour démarrer ou **❌ Annuler** pour abandonner."
            ),
            color=0x2ECC71
        )

        view = ConfirmLancementView(self, salon, createur, tous_joueurs, manches, debat)
        await interaction.channel.send(embed=embed_confirm, view=view)

        # Annonce dans le salon privé
        embed_bv = discord.Embed(
            title="🎮 Salon Imposteur créé !",
            description=(
                f"**Joueurs :** {', '.join(j.mention for j in tous_joueurs)}\n"
                f"**Manches :** {manches} | **Débat :** {debat}s\n\n"
                "En attente de confirmation du créateur pour démarrer..."
            ),
            color=0x9B59B6
        )
        await salon.send(embed=embed_bv)

    async def _lancer_partie(self, salon, createur, joueurs, manches_total, debat_duree):
        """Gère une partie complète d'imposteur."""
        # ✅ FIX 3 — Vérification que le jeu se lance bien dans un salon privé imposteur
        if not self.is_salon_prive_imposteur(salon.id):
            await salon.send("❌ Ce jeu doit se dérouler dans un salon privé imposteur.")
            return

        scores = {j.id: 0 for j in joueurs}
        partie_id = str(salon.id)

        for manche in range(1, manches_total + 1):
            # Attribution des mots
            paire = random.choice(MOTS_PAIRES)
            mot_majoritaire, mot_imposteur = paire if random.random() > 0.5 else (paire[1], paire[0])

            # Détermine l'imposteur
            idx_imposteur = random.randint(0, len(joueurs) - 1)
            imposteur = joueurs[idx_imposteur]

            mots_assigns = {}
            for j in joueurs:
                mots_assigns[j.id] = mot_imposteur if j == imposteur else mot_majoritaire

            # Annonce manche
            embed_m = discord.Embed(
                title=f"🎮 Manche {manche}/{manches_total}",
                description="Les mots secrets ont été envoyés en DM !\nChaque joueur va donner **1 mot lié** à son mot secret.",
                color=0x9B59B6
            )
            await salon.send(embed=embed_m)

            # Envoie les mots en DM
            dm_fails = []
            for j in joueurs:
                try:
                    await j.send(
                        f"🔒 **TON MOT SECRET — Manche {manche}** : **{mots_assigns[j.id]}**\n"
                        f"Donne **1 mot lié** à ce mot dans {salon.mention} quand c'est ton tour !"
                    )
                except:
                    dm_fails.append(j.mention)

            if dm_fails:
                await salon.send(f"⚠️ Impossible d'envoyer en DM à : {', '.join(dm_fails)}\nActive tes DMs !")

            # Phase mots
            mots_donnes = {}
            ordre = joueurs.copy()
            random.shuffle(ordre)

            for j in ordre:
                embed_tour = discord.Embed(
                    title="🔴 À toi !",
                    description="\n".join([
                        f"{'✅' if oj.id in mots_donnes else ('⏳' if oj == j else '❔')} {oj.display_name}"
                        + (f" → *{mots_donnes[oj.id]}*" if oj.id in mots_donnes else "")
                        for oj in ordre
                    ]),
                    color=0xE74C3C
                )
                embed_tour.set_footer(text=f"⏱️ 20s — {j.display_name}, tape ton mot !")
                msg_tour = await salon.send(f"{j.mention}", embed=embed_tour)

                def check_mot(m):
                    return m.author == j and m.channel == salon and not m.content.startswith("/")

                try:
                    rep_mot = await self.bot.wait_for("message", timeout=20, check=check_mot)
                    mot = rep_mot.content.strip().split()[0][:30]
                    mots_donnes[j.id] = mot
                    await rep_mot.delete()
                except asyncio.TimeoutError:
                    mots_donnes[j.id] = "*(pass)*"

                embed_tour.description = "\n".join([
                    f"{'✅' if oj.id in mots_donnes else '❔'} {oj.display_name}"
                    + (f" → *{mots_donnes[oj.id]}*" if oj.id in mots_donnes else "")
                    for oj in ordre
                ])
                await msg_tour.edit(embed=embed_tour)

            # Récap mots
            embed_mots = discord.Embed(
                title="📝 Mots donnés",
                description="\n".join([
                    f"**{j.display_name}** → *{mots_donnes.get(j.id, '?')}*"
                    for j in ordre
                ]),
                color=0x3498DB
            )
            await salon.send(embed=embed_mots)

            # Phase débat
            if debat_duree > 0:
                await salon.send(
                    f"💬 **DÉBAT** — {debat_duree}s ! Discutez et suspectez l'imposteur !\n"
                    f"Vote précoce : `/vote-prec @suspect`"
                )
                await asyncio.sleep(debat_duree)

            # Phase vote
            votes_db = {}
            embed_vote = discord.Embed(
                title="🔒 Vote secret",
                description=f"Votez avec `/vote @membre` — **[0/{len(joueurs)}]**",
                color=0xF1C40F
            )
            msg_vote = await salon.send(embed=embed_vote)
            self.parties_actives[partie_id] = {
                "votes": votes_db, "joueurs": joueurs,
                "msg_vote": msg_vote, "imposteur": imposteur
            }

            await asyncio.sleep(30)

            # Résultats vote
            if votes_db:
                compte = {}
                for _, cible_id in votes_db.items():
                    compte[cible_id] = compte.get(cible_id, 0) + 1
                suspect_id = max(compte, key=compte.get)
                suspect = discord.utils.get(joueurs, id=int(suspect_id))
            else:
                suspect = None

            # Calcul points
            n = len(joueurs)
            pts_detecteur = n - 1
            detecteurs = []
            premiers = []

            if suspect and suspect == imposteur:
                bons_votes = [(uid, cid) for uid, cid in votes_db.items() if str(cid) == str(imposteur.id)]
                for i, (uid, _) in enumerate(bons_votes):
                    voteur = discord.utils.get(joueurs, id=int(uid))
                    if voteur and voteur != imposteur:
                        detecteurs.append(voteur)
                        pts = pts_detecteur * 1.5 if i == 0 else pts_detecteur
                        scores[voteur.id] = scores.get(voteur.id, 0) + pts
                        if i == 0:
                            premiers.append(voteur)

            # Embed révélation
            embed_rev = discord.Embed(
                title=f"🏆 RÉSULTATS — Manche {manche}/{manches_total}",
                color=0xF1C40F
            )
            embed_rev.add_field(
                name="🦹 IMPOSTEUR",
                value=f"{imposteur.mention}\n*Mot : {mot_imposteur}*",
                inline=False
            )
            embed_rev.add_field(name="📖 Mot majoritaire", value=f"**{mot_majoritaire}**", inline=True)

            if detecteurs:
                embed_rev.add_field(
                    name=f"✅ Détecteurs (+{pts_detecteur} pts)",
                    value="\n".join(j.mention for j in detecteurs),
                    inline=False
                )
            if premiers:
                embed_rev.add_field(name="🔥 Précoce +7.5 pts", value=premiers[0].mention, inline=True)

            classement = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            cl_txt = "\n".join([
                f"{'🥇🥈🥉'[i] if i < 3 else f'{i+1}.'} "
                f"{discord.utils.get(joueurs, id=uid).display_name} : **{pts} pts**"
                for i, (uid, pts) in enumerate(classement)
            ])
            embed_rev.add_field(name="📊 Classement", value=cl_txt or "—", inline=False)
            embed_rev.set_footer(
                text="Prochaine manche dans 5s..." if manche < manches_total else "Partie terminée !"
            )
            await salon.send(embed=embed_rev)

            if manche < manches_total:
                await asyncio.sleep(5)

        # Fin de partie
        gagnant_id, gagnant_pts = max(scores.items(), key=lambda x: x[1])
        gagnant = discord.utils.get(joueurs, id=gagnant_id)
        embed_fin = discord.Embed(
            title="🎉 PARTIE TERMINÉE !",
            description=f"🏆 **Vainqueur : {gagnant.mention}** avec **{gagnant_pts} pts** !",
            color=0x2ECC71
        )
        cl_final = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        embed_fin.add_field(
            name="Classement final",
            value="\n".join([
                f"{'🥇🥈🥉'[i] if i < 3 else f'{i+1}.'} "
                f"{discord.utils.get(joueurs, id=uid).display_name} — {pts} pts"
                for i, (uid, pts) in enumerate(cl_final)
            ]),
            inline=False
        )
        embed_fin.set_footer(text="Utilise /suppr pour fermer ce salon | /imposteur pour rejouer")

        # Sauvegarde stats
        imp_db = load_imp_db()
        for uid, pts in scores.items():
            key = str(uid)
            if key not in imp_db:
                imp_db[key] = {"points": 0, "parties": 0}
            imp_db[key]["points"] += pts
            imp_db[key]["parties"] += 1
        save_imp_db(imp_db)

        view = FinPartieView(self, salon, createur, joueurs, manches_total, debat_duree)
        await salon.send(embed=embed_fin, view=view)
        self.parties_actives.pop(partie_id, None)

    # ── VOTE ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="vote", description="Vote secret contre un suspect")
    @app_commands.describe(suspect="Le membre que tu suspectes d'être l'imposteur")
    async def slash_vote(self, interaction: discord.Interaction, suspect: discord.Member):
        partie_id = str(interaction.channel.id)
        if partie_id not in self.parties_actives:
            await interaction.response.send_message("❌ Pas de partie en cours ici.", ephemeral=True)
            return
        partie = self.parties_actives[partie_id]
        if interaction.user.id not in {j.id for j in partie["joueurs"]}:
            await interaction.response.send_message("❌ Tu ne participes pas à cette partie.", ephemeral=True)
            return
        if str(interaction.user.id) in partie["votes"]:
            await interaction.response.send_message("❌ Tu as déjà voté !", ephemeral=True)
            return
        partie["votes"][str(interaction.user.id)] = str(suspect.id)
        nb = len(partie["votes"])
        total = len(partie["joueurs"])
        try:
            msg_v = partie["msg_vote"]
            embed = msg_v.embeds[0]
            embed.description = f"Votez avec `/vote @membre` — **[{nb}/{total}]**"
            await msg_v.edit(embed=embed)
        except:
            pass
        await interaction.response.send_message(
            f"✅ Vote pour **{suspect.display_name}** enregistré !", ephemeral=True
        )

    @app_commands.command(name="vote-prec", description="Vote précoce (bonus x1.5) pendant le débat")
    @app_commands.describe(suspect="Le membre que tu suspectes")
    async def slash_vote_prec(self, interaction: discord.Interaction, suspect: discord.Member):
        await self.slash_vote.callback(self, interaction, suspect)

    @app_commands.command(name="classement-imposteur", description="Classement du jeu Imposteur Mots")
    async def slash_classement_imp(self, interaction: discord.Interaction):
        await interaction.response.defer()
        imp_db = load_imp_db()
        data_list = []
        for uid, d in imp_db.items():
            m = interaction.guild.get_member(int(uid))
            if m:
                data_list.append((m.display_name, d["points"], d["parties"]))
        data_list.sort(key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        lines = [
            f"{medals[i]} **{n}** — {p} pts ({part} parties)"
            for i, (n, p, part) in enumerate(data_list[:10])
        ]
        embed = discord.Embed(
            title="🏆 Classement — Imposteur Mots",
            description="\n".join(lines) or "Aucune partie.",
            color=0x9B59B6
        )
        await interaction.followup.send(embed=embed)

    # ── GESTION SALON PRIVÉ ───────────────────────────────────────────────────
    @app_commands.command(name="suppr", description="Supprime ton salon imposteur privé")
    async def slash_suppr(self, interaction: discord.Interaction):
        if interaction.user.id not in self.salons_prives:
            await interaction.response.send_message(
                "❌ Tu n'as pas de salon imposteur actif.", ephemeral=True
            )
            return
        ch_id = self.salons_prives[interaction.user.id]
        if interaction.channel.id != ch_id:
            ch = interaction.guild.get_channel(ch_id)
            mention = ch.mention if ch else "salon introuvable"
            await interaction.response.send_message(
                f"❌ Utilise cette commande dans ton salon imposteur : {mention}", ephemeral=True
            )
            return
        await interaction.response.send_message("🗑️ Salon supprimé dans 5s...")
        await asyncio.sleep(5)
        del self.salons_prives[interaction.user.id]
        try:
            await interaction.channel.delete(reason="Suppression par le propriétaire")
        except:
            pass

    @app_commands.command(name="invite", description="Invite un membre dans ton salon imposteur")
    @app_commands.describe(membre="Le membre à inviter")
    async def slash_invite(self, interaction: discord.Interaction, membre: discord.Member):
        if interaction.user.id not in self.salons_prives or \
                self.salons_prives[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Pas de salon imposteur actif ici.", ephemeral=True
            )
            return
        await interaction.channel.set_permissions(
            membre, view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.response.send_message(f"✅ {membre.mention} a été invité !")

    @app_commands.command(name="kick-salon", description="Expulse un membre de ton salon imposteur")
    @app_commands.describe(membre="Le membre à expulser")
    async def slash_kick_salon(self, interaction: discord.Interaction, membre: discord.Member):
        if interaction.user.id not in self.salons_prives or \
                self.salons_prives[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Pas de salon imposteur actif ici.", ephemeral=True
            )
            return
        await interaction.channel.set_permissions(membre, view_channel=False)
        await interaction.response.send_message(f"✅ {membre.mention} a été expulsé du salon.")

    @app_commands.command(name="lock-salon", description="Verrouille ton salon (plus de nouveaux membres)")
    async def slash_lock_salon(self, interaction: discord.Interaction):
        if interaction.user.id not in self.salons_prives or \
                self.salons_prives[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Pas de salon imposteur actif ici.", ephemeral=True
            )
            return
        await interaction.channel.set_permissions(
            interaction.guild.default_role, view_channel=False, send_messages=False
        )
        await interaction.response.send_message("🔒 Salon verrouillé — plus aucun nouveau membre.")

    @app_commands.command(name="rename-salon", description="Renomme ton salon imposteur")
    @app_commands.describe(nom="Nouveau nom du salon")
    async def slash_rename_salon(self, interaction: discord.Interaction, nom: str):
        if interaction.user.id not in self.salons_prives or \
                self.salons_prives[interaction.user.id] != interaction.channel.id:
            await interaction.response.send_message(
                "❌ Pas de salon imposteur actif ici.", ephemeral=True
            )
            return
        try:
            await interaction.channel.edit(name=nom[:100])
            await interaction.response.send_message(f"✅ Salon renommé en **{nom}** !")
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)


# ============================================================
# VUE FIN DE PARTIE
# ============================================================
class FinPartieView(discord.ui.View):
    def __init__(self, cog, salon, createur, joueurs, manches, debat):
        super().__init__(timeout=120)
        self.cog = cog
        self.salon = salon
        self.createur = createur
        self.joueurs = joueurs
        self.manches = manches
        self.debat = debat

    @discord.ui.button(label="🔄 Nouvelle partie", style=discord.ButtonStyle.green)
    async def nouvelle_partie(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.createur:
            await interaction.response.send_message(
                "❌ Seul le créateur peut relancer.", ephemeral=True
            )
            return
        await interaction.response.send_message("🎮 Relancement de la partie...")
        self.stop()
        await self.cog._lancer_partie(
            self.salon, self.createur, self.joueurs, self.manches, self.debat
        )

    @discord.ui.button(label="❌ Fermer le salon", style=discord.ButtonStyle.red)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.createur:
            await interaction.response.send_message(
                "❌ Seul le créateur peut fermer.", ephemeral=True
            )
            return
        await interaction.response.send_message("🗑️ Fermeture dans 5s...")
        self.stop()
        await asyncio.sleep(5)
        self.cog.salons_prives.pop(self.createur.id, None)
        try:
            await self.salon.delete(reason="Fin de partie")
        except:
            pass


async def setup(bot):
    await bot.add_cog(Imposteur(bot))
