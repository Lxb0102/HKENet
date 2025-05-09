import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from outer_models import RetNetRelPos as RNP
# from outer_models import MolecularGraphNeuralNetwork
from outer_models import GCN
import random
# from HyperGCN import HyperGraphConvolution as HGCN
from layers import DisentangleGraph as DGCN
from torchVersion.Model import Model as HCCF
from outer_models import trd_encoder
torch.manual_seed(1203)

class demo_net(nn.Module):
    def __init__(self,voc_size,emb_dim=64,nhead=2,device='cpu',dm_mat=None,pm_mat=None,ehr_adj=None):
        super().__init__()
        self.device = device
        self.emb_dim = emb_dim
        self.med_dim = emb_dim*2
        self.voc_size = voc_size
        self.nhead = 2
        self.rnp = RNP(emb_dim=emb_dim*4)
        self.diag_emb = nn.Embedding(voc_size[0],emb_dim,padding_idx=0,device=device)
        self.proc_emb = nn.Embedding(voc_size[1],emb_dim,padding_idx=0,device=device)
        self.ehr_adj=torch.tensor(ehr_adj,device=self.device,dtype=torch.float)
        self.dropout = nn.Dropout(p=0.2)

        self.diag_linear_1 = nn.Sequential(*[nn.Linear(emb_dim,emb_dim*2,device=device),
                                           nn.Tanh(),
                                           nn.Linear(emb_dim*2,emb_dim,device=device),
                                           nn.Dropout(0.3)])


        self.proc_linear_1 = nn.Sequential(*[nn.Linear(emb_dim,emb_dim*2,device=device),
                                           nn.Tanh(),
                                           nn.Linear(emb_dim*2,emb_dim,device=device),
                                           nn.Dropout(0.3)])

        self.diag_linear_2 = nn.Sequential(*[nn.Linear(emb_dim, emb_dim * 2, device=device),
                                             nn.Tanh(),
                                             nn.Linear(emb_dim * 2, int(self.med_dim / 2), device=device),
                                             nn.Dropout(0.3)])

        self.proc_linear_2 = nn.Sequential(*[nn.Linear(emb_dim, emb_dim * 2, device=device),
                                             nn.Tanh(),
                                             nn.Linear(emb_dim * 2, int(self.med_dim / 2), device=device),
                                             nn.Dropout(0.3)])

        self.final_linear = nn.Sequential(*[nn.Linear(self.med_dim,self.med_dim*4,device=device),
                                           nn.Tanh(),
                                           nn.Linear(self.med_dim*4,self.med_dim*4,device=device),
                                           nn.Tanh(),
                                            nn.Linear(self.med_dim*4, self.med_dim, device=device),
                                           nn.Dropout(0.3)])

        self.seq_med = nn.Parameter(torch.randn([self.med_dim,voc_size[2]-1],device=device))
        self.vst_med = nn.Parameter(torch.randn([self.med_dim,voc_size[2]-1],device=device))

        self.diag_med_block = nn.Parameter(torch.randn([self.emb_dim, voc_size[2] - 1], device=device))
        self.proc_med_block = nn.Parameter(torch.randn([self.emb_dim, voc_size[2] - 1], device=device))

        self.diag_encoder = nn.TransformerEncoderLayer(emb_dim, self.nhead, batch_first=True, dropout=0.2,device=device)
        self.proc_encoder = nn.TransformerEncoderLayer(emb_dim, self.nhead, batch_first=True, dropout=0.2,device=device)

        self.diag_integ = trd_encoder(emb_dim=int(self.med_dim/2),device=device)
        self.proc_integ = trd_encoder(emb_dim=int(self.med_dim/2),device=device)

        self.diag_prob_integ = trd_encoder(emb_dim=int(voc_size[2]-1), device=device)
        self.proc_prob_integ = trd_encoder(emb_dim=int(voc_size[2]-1), device=device)

        self.gender_block = nn.Parameter(torch.eye(voc_size[2]-1,device=device,requires_grad=True))
        # self.female_block = nn.Parameter(torch.eye(voc_size[2], device=device, requires_grad=True))

        self.patient_linear = nn.Sequential(*[nn.Linear(self.med_dim, self.med_dim, device=device),
                                              nn.Tanh()
                                              ])
        self.age_block = nn.Parameter(torch.randn(self.med_dim,voc_size[2],device=device,requires_grad=True))

        self.patient_mem_contact = nn.Sequential(*[nn.Linear(self.med_dim*2,self.med_dim*4,device=device),
                                           nn.Tanh(),
                                           nn.Linear(self.med_dim*4,voc_size[2]-1,device=device),
                                           nn.Tanh(),
                                           nn.Dropout(0.2)])
        self.drug_mem_integ = trd_encoder(emb_dim=int(voc_size[2]-1), device=device)

        self.diag_his_encoder = nn.GRU(int(self.med_dim / 2), int(self.med_dim / 2),
                                       batch_first=False, device=device,dropout=0.2)
        self.proc_his_encoder = nn.GRU(int(self.med_dim / 2), int(self.med_dim / 2),
                                       batch_first=False, device=device,dropout=0.2)
        self.vst_med_block = nn.Parameter(torch.randn([self.med_dim,voc_size[2]-1],device=device))

        self.vst_his_contact = nn.Sequential(*[nn.Linear(self.med_dim * 2, self.med_dim * 4, device=device),
                                                   nn.Tanh(),
                                                   nn.Linear(self.med_dim * 4, voc_size[2] - 1, device=device),
                                                   nn.Tanh(),
                                                   nn.Dropout(0.2)])

        self.prob_seq_contact = nn.Sequential(*[nn.Linear((voc_size[2]-1)*4, (voc_size[2]-1)*8, device=device),
                                               nn.Tanh(),
                                               nn.Linear((voc_size[2]-1)* 8, (voc_size[2]-1), device=device),
                                               nn.Tanh(),
                                               nn.Dropout(0.2)])

    def encoder(self,diag_seq,proc_seq,diag_mask=None,proc_mask=None):
        max_diag_num = diag_seq.size()[-1]
        max_proc_num = proc_seq.size()[-1]
        max_visit_num = diag_seq.size()[1]
        batch_size = diag_seq.size()[0]
        diag_seq = self.diag_linear_1(self.diag_emb(diag_seq).view(batch_size * max_visit_num,
                                                                   max_diag_num, self.emb_dim))
        proc_seq = self.proc_linear_1(self.proc_emb(proc_seq).view(batch_size * max_visit_num,
                                                                   max_proc_num, self.emb_dim))

        d_mask_matrix = diag_mask.view(batch_size * max_visit_num, max_diag_num).unsqueeze(dim=1).unsqueeze(
            dim=1).repeat(1, self.nhead, max_diag_num, 1)  # [batch*seq, nhead, input_length, output_length]
        d_mask_matrix = d_mask_matrix.view(batch_size * max_visit_num * self.nhead, max_diag_num, max_diag_num)

        p_mask_matrix = proc_mask.view(batch_size * max_visit_num, max_proc_num).unsqueeze(dim=1).unsqueeze(
            dim=1).repeat(1, self.nhead, max_proc_num, 1)
        p_mask_matrix = p_mask_matrix.view(batch_size * max_visit_num * self.nhead, max_proc_num, max_proc_num)

        diag_seq = self.diag_encoder(diag_seq,src_mask=d_mask_matrix).view(-1, max_diag_num,
                                                                           self.emb_dim)
        proc_seq = self.proc_encoder(proc_seq, src_mask=p_mask_matrix).view(-1, max_proc_num,
                                                                            self.emb_dim)

        diag_rep_1,diag_rep_2 = self.diag_integ(diag_seq)
        diag_rep = diag_rep_1+diag_rep_2
        proc_rep_1, proc_rep_2 = self.proc_integ(proc_seq)
        proc_rep = proc_rep_1 + proc_rep_2
        patient_rep = torch.concat([diag_rep,proc_rep],dim=-1)

        patient_rep = self.final_linear(patient_rep)
        # print('==================================================')
        # print(diag_seq)
        diag_seq = self.diag_linear_2(diag_seq)
        proc_seq = self.proc_linear_2(proc_seq)
        # print('**************************************************')
        # print(diag_seq)

        # diag_seq = self.diag_prob_integ(diag_seq)[1].transpose(0,1)
        # proc_seq = self.proc_prob_integ(proc_seq)[1].transpose(0,1)

        return diag_seq,proc_seq,patient_rep
    def decoder(self,diag,proc,ages,gender=None,drug_mem=None,patient_rep=None):
        patient_rep_simple=patient_rep
        drug_mem = torch.nn.functional.one_hot(drug_mem.squeeze(dim=0),
                                               num_classes=self.voc_size[2]).sum(dim=-2)[:, 1:].to(torch.float32)

        drug_mem_pad = torch.zeros_like(drug_mem[0]).unsqueeze(dim=0)

        drug_mem = torch.cat([drug_mem_pad, drug_mem], dim=0)[:drug_mem.size()[0]].unsqueeze(dim=0).repeat([
            patient_rep.size()[0], 1, 1]).to(self.device)

        #+++++++++++++++++++++++++++++++++++++++++++++++++++++
        diag_his = diag.sum(dim=-2).reshape(-1,self.emb_dim)
        proc_his = proc.sum(dim=-2).reshape(-1,self.emb_dim)

        diag_his = self.diag_his_encoder(diag_his)[0]
        proc_his = self.proc_his_encoder(proc_his)[0]

        vst_patient = torch.concat([diag_his,proc_his],dim=-1)

        vst_his_enhance = self.history_gate_unit(vst_patient,drug_mem,self.vst_his_contact)
        #==============================================
        #+++++++++++++++++++++++++++++++++++++++++++++
        patient_rep = patient_rep.squeeze(dim=1)
        his_enhance = self.history_gate_unit(patient_rep, drug_mem, self.patient_mem_contact)
        #============================================================
        seq_med = self.seq_med_gcn(self.ehr_adj,0.2)
        vst_med = self.vst_med_gcn(self.ehr_adj,0.2)
        DPI_prob = patient_rep_simple@seq_med[0].T
        final_prob_1 = patient_rep@seq_med[0].T
        final_prob_2 = vst_patient@vst_med[0].T
        diag_probseq = diag @ self.diag_med_block
        proc_probseq = proc @ self.proc_med_block
        # +++++++++++++++++++++++++++++++++++++++++++++++++++++
        diag_prob_1, diag_prob_2 = self.diag_prob_integ(diag_probseq)
        diag_prob = (diag_prob_1+diag_prob_2).reshape(-1,self.voc_size[2]-1)

        proc_prob_1, proc_prob_2 = self.proc_prob_integ(proc_probseq)
        proc_prob = (proc_prob_1 + proc_prob_2).reshape(-1,self.voc_size[2]-1)
        final_prob_3 = diag_prob + proc_prob

        prob_seq_rep = torch.concat([diag_prob, proc_prob], dim=-1)
        prob_seq_enhance = self.history_gate_unit(prob_seq_rep, drug_mem, self.prob_seq_contact)
        # ==============================================
        # 去除DPI
        # prob = final_prob_1 + his_enhance + final_prob_2 + 1.2*vst_his_enhance + final_prob_3 #+ prob_seq_enhance
        prob = DPI_prob
       
        prob = prob.reshape(-1,self.voc_size[2]-1)
        
        prob = F.sigmoid(prob)

        # prob_patient_integ_out = (final_prob_1 + his_enhance).reshape(-1, self.voc_size[2] - 1)
        # prob_patient_integ_out_padder = torch.full_like(prob.T[0], 0).unsqueeze(dim=0).T
        # prob_patient_integ_out = torch.cat([prob_patient_integ_out_padder, prob_patient_integ_out], dim=-1)
        #
        # his_seq_out = (final_prob_1 + his_enhance).reshape(-1, self.voc_size[2] - 1)
        # his_seq_out_padder = torch.full_like(prob.T[0], 0).unsqueeze(dim=0).T
        # his_seq_out = torch.cat([his_seq_out_padder, his_seq_out], dim=-1)

        prob_padder = torch.full_like(prob.T[0], 0).unsqueeze(dim=0).T
        prob = torch.cat([prob_padder, prob], dim=-1)

        return prob,prob*prob.T.unsqueeze(dim=-1),\
               [diag_prob_1,diag_prob_2],\
               [proc_prob_1,proc_prob_2]
               # F.sigmoid(prob_patient_integ_out),\
               # F.sigmoid(his_seq_out)

    def forward(self,input,diag_mask=None,proc_mask=None,ages=None,gender=None,drug_mem=None):

        diag_hid,proc_hid,patient_rep = self.encoder(input[0],input[1],input[3],input[4])
        decoder_out = self.decoder(diag_hid,proc_hid,input[5],input[6],input[2],patient_rep=patient_rep)

        return decoder_out

