import time
import pprint
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical

class PPO(nn.Module):
    def __init__(self, arg_dict, device=None):
        super(PPO, self).__init__()
        self.device=None
        if device:
            self.device = device

        self.arg_dict = arg_dict

        self.fc_player = nn.Linear(arg_dict["feature_dims"]["player"],128)
        self.fc_player2 = nn.Linear(128,128)       
        
        self.fc_ball = nn.Linear(arg_dict["feature_dims"]["ball"],96)
        self.fc_ball2 = nn.Linear(96,96)
        
        self.fc_left = nn.Linear(arg_dict["feature_dims"]["left_team"],128)
        self.fc_left2 = nn.Linear(128,128)
        self.fc_left_fin = nn.Linear(256,128)
        self.fc_right  = nn.Linear(arg_dict["feature_dims"]["right_team"],128)
        self.fc_right2  = nn.Linear(128,128)
        self.fc_right_fin = nn.Linear(256,128)
        
        self.fc_player_left_q = nn.Linear(128,64)
        self.fc_left_k = nn.Linear(128,64)
        self.fc_player_right_q = nn.Linear(128,64)
        self.fc_right_k = nn.Linear(128,64)
        
        self.fc_player_left_q2 = nn.Linear(128,64)
        self.fc_left_k2 = nn.Linear(128,64)
        self.fc_player_right_q2 = nn.Linear(128,64)
        self.fc_right_k2 = nn.Linear(128,64)

        
        self.fc_cat = nn.Linear(128+96+128+128,arg_dict["lstm_size"])
        
        self.norm_player = nn.LayerNorm(128)
        self.norm_player2 = nn.LayerNorm(128)
        self.norm_ball = nn.LayerNorm(96)
        self.norm_ball2 = nn.LayerNorm(96)
        self.norm_left = nn.LayerNorm(128)
        self.norm_left2 = nn.LayerNorm(128)
        self.norm_left_fin = nn.LayerNorm(128)
        self.norm_right = nn.LayerNorm(128)
        self.norm_right2 = nn.LayerNorm(128)
        self.norm_right_fin = nn.LayerNorm(128)

        self.norm_cat = nn.LayerNorm(arg_dict["lstm_size"])
        
        self.lstm  = nn.LSTM(arg_dict["lstm_size"],arg_dict["lstm_size"])

        self.fc_pi_a1 = nn.Linear(arg_dict["lstm_size"], 164)
        self.fc_pi_a2 = nn.Linear(164, 12)
        self.norm_pi_a1 = nn.LayerNorm(164)
        
        self.fc_pi_m1 = nn.Linear(arg_dict["lstm_size"], 164)
        self.fc_pi_m2 = nn.Linear(164, 8)
        self.norm_pi_m1 = nn.LayerNorm(164)

        self.fc_v1 = nn.Linear(arg_dict["lstm_size"], 164)
        self.norm_v1 = nn.LayerNorm(164)
        self.fc_v2 = nn.Linear(164, 1,  bias=False)
        self.optimizer = optim.Adam(self.parameters(), lr=arg_dict["learning_rate"])

        self.gamma = arg_dict["gamma"]
        self.K_epoch = arg_dict["k_epoch"]
        self.lmbda = arg_dict["lmbda"]
        self.eps_clip = 0.1
        self.entropy_coef = arg_dict["entropy_coef"]
        self.move_entropy_coef = arg_dict["move_entropy_coef"]
        
    def forward(self, state_dict):
        player_state = state_dict["player"]          
        ball_state = state_dict["ball"]              
        left_team_state = state_dict["left_team"]
        left_closest_state = state_dict["left_closest"]
        right_team_state = state_dict["right_team"]  
        right_closest_state = state_dict["right_closest"]
        avail = state_dict["avail"]
        
        player_embed = F.relu(self.norm_player(self.fc_player(player_state)))
        player_embed = self.norm_player2(self.fc_player2(player_embed))
        ball_embed   = F.relu(self.norm_ball(self.fc_ball(ball_state)))
        ball_embed   = self.norm_ball2(self.fc_ball2(ball_embed))
        
        left_team_embed = F.relu(self.norm_left(self.fc_left(left_team_state)))  # horizon, batch, n, dim
        left_team_embed = F.relu(self.norm_left2(self.fc_left2(left_team_embed)))  # horizon, batch, n, dim
        
        right_team_embed = F.relu(self.norm_right(self.fc_right(right_team_state)))
        right_team_embed = F.relu(self.norm_right2(self.fc_right2(right_team_embed)))
        
        player_left_q = self.fc_player_left_q(player_embed)                                # horizon, batch, dim
        left_team_k = self.fc_left_k(left_team_embed)                                      # horizon, batch, n, dim
        [horizon, batch_size, n_player, f_dim] = left_team_k.size()
        player_left_q = player_left_q.view(horizon*batch_size, 1, f_dim)                   # horizon*batch, 1,   dim1
        left_team_k = left_team_k.view(horizon*batch_size, n_player, f_dim).permute(0,2,1) # horizon*batch, dim1, n
        attention = F.softmax(torch.bmm(player_left_q, left_team_k)/8, dim=2)                # horizon*batch, 1    , n
        attention = attention.view(horizon, batch_size, -1).unsqueeze(3)                   # horizon, batch, n, 1
        left_team = left_team_embed*attention                                              # horizon, batch, n, dim
        left_team = left_team.permute(0,1,3,2)
        left_team = torch.sum(left_team, axis=3)
        
        player_left_q2 = self.fc_player_left_q2(player_embed)                                # horizon, batch, dim
        left_team_k2 = self.fc_left_k2(left_team_embed)                                      # horizon, batch, n, dim
        [horizon, batch_size, n_player, f_dim] = left_team_k2.size()
        player_left_q2 = player_left_q2.view(horizon*batch_size, 1, f_dim)                   # horizon*batch, 1,   dim1
        left_team_k2 = left_team_k2.view(horizon*batch_size, n_player, f_dim).permute(0,2,1) # horizon*batch, dim1, n
        attention2 = F.softmax(torch.bmm(player_left_q2, left_team_k2)/8, dim=2)                # horizon*batch, 1    , n
        attention2 = attention2.view(horizon, batch_size, -1).unsqueeze(3)                   # horizon, batch, n, 1
        left_team2 = left_team_embed*attention2                                              # horizon, batch, n, dim
        left_team2 = left_team2.permute(0,1,3,2)                                             # horizon, batch, dim, n
        left_team2 = torch.sum(left_team2, axis=3)                                           # horizon, batch, dim
        
        left_team_fin = torch.cat([left_team, left_team2], axis=2)
        left_team_fin = self.norm_left_fin(self.fc_left_fin(left_team_fin))
        
        player_right_q = self.fc_player_right_q(player_embed)                                # horizon, batch, dim
        right_team_k = self.fc_right_k(right_team_embed)                                      # horizon, batch, n, dim
        [horizon, batch_size, n_player, f_dim] = right_team_k.size()
        player_right_q = player_right_q.view(horizon*batch_size, 1, f_dim)                   # horizon*batch, 1,   dim1
        right_team_k = right_team_k.view(horizon*batch_size, n_player, f_dim).permute(0,2,1) # horizon*batch, dim1, n
        attention = F.softmax(torch.bmm(player_right_q, right_team_k)/8, dim=2)                # horizon*batch, 1    , n
        attention = attention.view(horizon, batch_size, -1).unsqueeze(3)                   # horizon, batch, n, 1
        right_team = right_team_embed*attention                                              # horizon, batch, n, dim
        right_team = right_team.permute(0,1,3,2)
        right_team = torch.sum(right_team, axis=3)
        
        player_right_q2 = self.fc_player_right_q2(player_embed)                                # horizon, batch, dim
        right_team_k2 = self.fc_right_k2(right_team_embed)                                      # horizon, batch, n, dim
        [horizon, batch_size, n_player, f_dim] = right_team_k2.size()
        player_right_q2 = player_right_q2.view(horizon*batch_size, 1, f_dim)                   # horizon*batch, 1,   dim1
        right_team_k2 = right_team_k2.view(horizon*batch_size, n_player, f_dim).permute(0,2,1) # horizon*batch, dim1, n
        attention2 = F.softmax(torch.bmm(player_right_q2, right_team_k2)/8, dim=2)                # horizon*batch, 1    , n
        attention2 = attention2.view(horizon, batch_size, -1).unsqueeze(3)                   # horizon, batch, n, 1
        right_team2 = right_team_embed*attention2                                              # horizon, batch, n, dim
        right_team2 = right_team2.permute(0,1,3,2)
        right_team2 = torch.sum(right_team2, axis=3)
        
        right_team_fin = torch.cat([right_team, right_team2], axis=2)
        right_team_fin = self.norm_right_fin(self.fc_right_fin(right_team_fin))
        
        cat = torch.cat([player_embed, ball_embed, left_team_fin, right_team_fin], 2)
        cat = F.relu(self.norm_cat(self.fc_cat(cat)))
        h_in = state_dict["hidden"]
        out, h_out = self.lstm(cat, h_in)
        
        a_out = F.relu(self.norm_pi_a1(self.fc_pi_a1(out)))
        a_out = self.fc_pi_a2(a_out)
        logit = a_out + (avail-1)*1e7
        prob = F.softmax(logit, dim=2)
        
        prob_m = F.relu(self.norm_pi_m1(self.fc_pi_m1(out)))
        prob_m = self.fc_pi_m2(prob_m)
        prob_m = F.softmax(prob_m, dim=2)

        v = F.relu(self.norm_v1(self.fc_v1(out)))
        v = self.fc_v2(v)

        return prob, prob_m, v, h_out

    def make_batch(self, data):
        # data = [tr1, tr2, ..., tr10] * batch_size
        s_player_batch, s_ball_batch, s_left_batch, s_left_closest_batch, s_right_batch, s_right_closest_batch, avail_batch =  [],[],[],[],[],[],[]
        s_player_prime_batch, s_ball_prime_batch, s_left_prime_batch, s_left_closest_prime_batch, \
                                                  s_right_prime_batch, s_right_closest_prime_batch, avail_prime_batch =  [],[],[],[],[],[],[]
        h1_in_batch, h2_in_batch, h1_out_batch, h2_out_batch = [], [], [], []
        a_batch, m_batch, r_batch, prob_batch, done_batch, need_move_batch = [], [], [], [], [], []
        
        for rollout in data:
            s_player_lst, s_ball_lst, s_left_lst, s_left_closest_lst, s_right_lst, s_right_closest_lst, avail_lst =  [], [], [], [], [], [], []
            s_player_prime_lst, s_ball_prime_lst, s_left_prime_lst, s_left_closest_prime_lst, \
                                                  s_right_prime_lst, s_right_closest_prime_lst, avail_prime_lst =  [], [], [], [], [], [], []
            h1_in_lst, h2_in_lst, h1_out_lst, h2_out_lst = [], [], [], []
            a_lst, m_lst, r_lst, prob_lst, done_lst, need_move_lst = [], [], [], [], [], []
            
            for transition in rollout:
                s, a, m, r, s_prime, prob, done, need_move = transition

                s_player_lst.append(s["player"])
                s_ball_lst.append(s["ball"])
                s_left_lst.append(s["left_team"])
                s_left_closest_lst.append(s["left_closest"])
                s_right_lst.append(s["right_team"])
                s_right_closest_lst.append(s["right_closest"])
                avail_lst.append(s["avail"])
                h1_in, h2_in = s["hidden"]
                h1_in_lst.append(h1_in)
                h2_in_lst.append(h2_in)
                
                s_player_prime_lst.append(s_prime["player"])
                s_ball_prime_lst.append(s_prime["ball"])
                s_left_prime_lst.append(s_prime["left_team"])
                s_left_closest_prime_lst.append(s_prime["left_closest"])
                s_right_prime_lst.append(s_prime["right_team"])
                s_right_closest_prime_lst.append(s_prime["right_closest"])
                avail_prime_lst.append(s_prime["avail"])
                h1_out, h2_out = s_prime["hidden"]
                h1_out_lst.append(h1_out)
                h2_out_lst.append(h2_out)

                a_lst.append([a])
                m_lst.append([m])
                r_lst.append([r])
                prob_lst.append([prob])
                done_mask = 0 if done else 1
                done_lst.append([done_mask])
                need_move_lst.append([need_move]),
                
            s_player_batch.append(s_player_lst)
            s_ball_batch.append(s_ball_lst)
            s_left_batch.append(s_left_lst)
            s_left_closest_batch.append(s_left_closest_lst)
            s_right_batch.append(s_right_lst)
            s_right_closest_batch.append(s_right_closest_lst)
            avail_batch.append(avail_lst)
            h1_in_batch.append(h1_in_lst[0])
            h2_in_batch.append(h2_in_lst[0])
            
            s_player_prime_batch.append(s_player_prime_lst)
            s_ball_prime_batch.append(s_ball_prime_lst)
            s_left_prime_batch.append(s_left_prime_lst)
            s_left_closest_prime_batch.append(s_left_closest_prime_lst)
            s_right_prime_batch.append(s_right_prime_lst)
            s_right_closest_prime_batch.append(s_right_closest_prime_lst)
            avail_prime_batch.append(avail_prime_lst)
            h1_out_batch.append(h1_out_lst[0])
            h2_out_batch.append(h2_out_lst[0])

            a_batch.append(a_lst)
            m_batch.append(m_lst)
            r_batch.append(r_lst)
            prob_batch.append(prob_lst)
            done_batch.append(done_lst)
            need_move_batch.append(need_move_lst)
        

        s = {
          "player": torch.tensor(s_player_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "ball": torch.tensor(s_ball_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "left_team": torch.tensor(s_left_batch, dtype=torch.float, device=self.device).permute(1,0,2,3),
          "left_closest": torch.tensor(s_left_closest_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "right_team": torch.tensor(s_right_batch, dtype=torch.float, device=self.device).permute(1,0,2,3),
          "right_closest": torch.tensor(s_right_closest_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "avail": torch.tensor(avail_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "hidden" : (torch.tensor(h1_in_batch, dtype=torch.float, device=self.device).squeeze(1).permute(1,0,2), 
                      torch.tensor(h2_in_batch, dtype=torch.float, device=self.device).squeeze(1).permute(1,0,2))
        }

        s_prime = {
          "player": torch.tensor(s_player_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "ball": torch.tensor(s_ball_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "left_team": torch.tensor(s_left_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2,3),
          "left_closest": torch.tensor(s_left_closest_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "right_team": torch.tensor(s_right_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2,3),
          "right_closest": torch.tensor(s_right_closest_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "avail": torch.tensor(avail_prime_batch, dtype=torch.float, device=self.device).permute(1,0,2),
          "hidden" : (torch.tensor(h1_out_batch, dtype=torch.float, device=self.device).squeeze(1).permute(1,0,2), 
                      torch.tensor(h2_out_batch, dtype=torch.float, device=self.device).squeeze(1).permute(1,0,2))
        }

        a,m,r,done_mask,prob,need_move = torch.tensor(a_batch, device=self.device).permute(1,0,2), \
                                         torch.tensor(m_batch, device=self.device).permute(1,0,2), \
                                         torch.tensor(r_batch, dtype=torch.float, device=self.device).permute(1,0,2), \
                                         torch.tensor(done_batch, dtype=torch.float, device=self.device).permute(1,0,2), \
                                         torch.tensor(prob_batch, dtype=torch.float, device=self.device).permute(1,0,2), \
                                         torch.tensor(need_move_batch, dtype=torch.float, device=self.device).permute(1,0,2)

        return s, a, m, r, s_prime, done_mask, prob, need_move
    

    def train_net(self, data):
        data_with_adv = []
        
        tot_loss_lst = []
        pi_loss_lst = []
        entropy_lst = []
        move_entropy_lst = []
        v_loss_lst = []
        
        for mini_batch in data:
            s, a, m, r, s_prime, done_mask, prob, need_move = mini_batch
            with torch.no_grad():
                pi, pi_move, v, _ = self.forward(s)
                pi_prime, pi_m_prime, v_prime, _ = self.forward(s_prime)

            td_target = r + self.gamma * v_prime * done_mask
            delta = td_target - v                           # [horizon * batch_size * 1]
            delta = delta.detach().cpu().numpy()

            advantage_lst = []
            advantage = np.array([0])
            for delta_t in delta[::-1]:
                advantage = self.gamma * self.lmbda * advantage + delta_t           
                advantage_lst.append(advantage)
            advantage_lst.reverse()
            advantage = torch.tensor(advantage_lst, dtype=torch.float, device=self.device)
            
            data_with_adv.append((s, a, m, r, s_prime, done_mask, prob, need_move, td_target, advantage))
        
        for i in range(self.K_epoch):
            for mini_batch in data_with_adv:
                s, a, m, r, s_prime, done_mask, prob, need_move, td_target, advantage = mini_batch
                pi, pi_move, v, _ = self.forward(s)
                pi_prime, pi_m_prime, v_prime, _ = self.forward(s_prime)
                
                pi_a = pi.gather(2,a)
                pi_m = pi_move.gather(2,m)
                pi_am = pi_a*(1-need_move + need_move*pi_m)
                ratio = torch.exp(torch.log(pi_am) - torch.log(prob))  # a/b == exp(log(a)-log(b))

                surr1 = ratio * advantage
                surr2 = torch.clamp(ratio, 1-self.eps_clip, 1+self.eps_clip) * advantage
                entropy = -torch.log(pi_am)
                move_entropy = -need_move*torch.log(pi_m)

                surr_loss = -torch.min(surr1, surr2)
                v_loss = F.smooth_l1_loss(v, td_target.detach())
                entropy_loss = -1*self.entropy_coef*entropy
                loss = surr_loss + v_loss + entropy_loss.mean()
                loss = loss.mean()
                
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.parameters(), 3.0)
                self.optimizer.step()
                
                tot_loss_lst.append(loss.item())
                pi_loss_lst.append(surr_loss.mean().item())
                v_loss_lst.append(v_loss.item())
                entropy_lst.append(entropy.mean().item())
                n_need_move = torch.sum(need_move).item()
                if n_need_move == 0:
                    move_entropy_lst.append(0)
                else:
                    move_entropy_lst.append((torch.sum(move_entropy)/n_need_move).item())
                
        return np.mean(tot_loss_lst), np.mean(pi_loss_lst), np.mean(v_loss_lst), np.mean(entropy_lst), np.mean(move_entropy_lst)
                