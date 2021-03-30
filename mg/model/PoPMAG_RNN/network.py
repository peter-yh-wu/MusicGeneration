import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical, Gumbel
from utils.data import flatten_padded_sequences
from collections import namedtuple
from utils.MuMIDI import MuMIDI_EventSeq
import numpy as np
from progress.bar import Bar
from PoPMAG_RNN .config import device
#device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class PoPMAG_RNN(nn.Module):
    def __init__(self, init_dim, event_dim, hidden_dim, bar_dim, embed_dim = 512,
                 rnn_layers=2, dropout=0.5):
        super().__init__()

        self.event_dim = event_dim
        self.init_dim = init_dim
        self.hidden_dim = hidden_dim
        self.rnn_layers = rnn_layers
        self.output_dim = event_dim
        self.embed_dim = embed_dim
        self.bar_dim = bar_dim

        self.primary_event = self.event_dim - 1
        self.inithid_fc = nn.Linear(init_dim, rnn_layers * hidden_dim)
        self.inithid_fc_activation = nn.Tanh()

        self.event_embedding = nn.Embedding(event_dim + bar_dim, embed_dim)
        # self.bar_embedding = nn.Embedding(bar_dim, embed_dim)

        self.encoder = nn.GRU(self.embed_dim, self.hidden_dim,
                          num_layers=rnn_layers, dropout=dropout)

        self.decoder = nn.GRU(self.embed_dim, self.hidden_dim,
                              num_layers=rnn_layers, dropout=dropout)
        #self.output_fc = nn.Linear(hidden_dim * rnn_layers, self.output_dim)
        self.output_fc = nn.ModuleList()
        for i in range(3):
            self.output_fc.append( nn.Linear(hidden_dim, self.output_dim) )

        self.output_fc_activation = nn.Softmax(dim=-1)

    def bar_embedding(self, n_bar):
        if n_bar < self.bar_dim:
            bar_embed = torch.tensor([self.event_dim + n_bar], dtype=torch.long, device=device)
        else:
            bar_embed = torch.tensor([self.event_dim + self.bar_dim - 1], dtype=torch.long, device=device)

        return self.event_embedding(bar_embed)

    def sequence_compression(self, input):
        """
        :param input: (batch) lists, stand for a sequence of bars, each bar is a sequence of number
        :return: output: (batch * bar_num * bar_len * embedding) padding array for training
        """
        # abs bar_embedding
        # position embedding
        # tempo class + tempo value
        # bar/position/chord/track/pitch+duration+velocity
        batch = len(input)
        batch_seqs = []
        mx_bar_num, mx_bar_len = 0, 0
        for bar_seqs in input:
            n_bar = 0
            one_bars = []
            for bar_items in bar_seqs:
                bar_embed = self.bar_embedding(n_bar)
                n_bar += 1

                i = 0
                pos_embed = 0
                tempo_embed = 0
                note_embed = 0

                bar_seq = []

                while i < len(bar_items):
                    if MuMIDI_EventSeq.check('position', bar_items[i]):
                        n_pos = torch.tensor([bar_items[i]], dtype=torch.long, device=device)
                        pos_embed = self.event_embedding(n_pos)
                        i += 1
                    elif i < len(bar_items) and MuMIDI_EventSeq.check('tempo_class', bar_items[i])\
                            and MuMIDI_EventSeq.check('tempo_value', bar_items[i + 1]):
                        tempo_cls = self.event_embedding( torch.tensor(bar_items[i], dtype=torch.long, device=device))
                        tempo_val = self.event_embedding( torch.tensor(bar_items[i + 1], dtype=torch.long, device=device))
                        tempo_embed = tempo_cls + tempo_val
                        i += 2

                    if i+2 < len(bar_items) and MuMIDI_EventSeq.check('note_on', bar_items[i])\
                        and MuMIDI_EventSeq.check('note_duration', bar_items[i + 1]) \
                        and MuMIDI_EventSeq.check('note_velocity', bar_items[i + 2]):
                        pitch = self.event_embedding(torch.tensor(bar_items[i], dtype=torch.long, device=device))
                        duration = self.event_embedding(torch.tensor(bar_items[i + 1], dtype=torch.long, device=device))
                        velocity = self.event_embedding(torch.tensor(bar_items[i + 2], dtype=torch.long, device=device))
                        note_embed = pitch + duration + velocity
                        i += 3
                    else:
                        note_embed = self.event_embedding(torch.tensor(bar_items[i], dtype=torch.long, device=device))
                        i += 1
                    elem_embed = bar_embed + pos_embed + tempo_embed + note_embed
                    bar_seq.append(elem_embed)
                # bar_seq = [ bar_len(vary) * embedding ]
                # pad_bar_seq = torch.nn.utils.rnn.pad_sequence(bar_seq, batch_first=True, padding_value=0)
                mx_bar_len = max(mx_bar_len, len(bar_seq))
                bar_seq = torch.cat(bar_seq)
                one_bars.append(bar_seq)
                # one_bars = [bar_num(vary) * bar_len(vary) * embedding]
            mx_bar_num = max(mx_bar_num, len(one_bars))
            batch_seqs.append(one_bars)

        pad_data = torch.zeros([batch, mx_bar_num, mx_bar_len, self.embed_dim], device=device)
        # print(pad_data.shape)
        pad_data_len = torch.zeros([batch, mx_bar_num])
        for batch_id in range(batch):
            one_bars = batch_seqs[batch_id]
            # print(f'len_one_bar={len(one_bars)}')
            for bar_num in range(len(one_bars)):
                # print(f'shape_bar_seq={one_bars[bar_num].shape}')
                bar_seq = one_bars[bar_num]
                pad_data[batch_id, bar_num, :len(bar_seq), :] = bar_seq
                pad_data_len[batch_id, bar_num] = len(bar_seq)
        # print(pad_data.shape)
        return pad_data, pad_data_len

    def compression(self, input):
        """
        :param input:  (batch * bar_num * bar_len * 7)
        :return: output: (batch * bar_num * bar_len * embedding) padding array for training
        """
        # print(input.dtype)
        # print(input.device)
        input_embed = self.event_embedding(input)
        return torch.sum(input_embed, dim = -2)


    def forward(self, event, hidden=None):
        # One step forward
        assert len(event.shape) == 2
        assert event.shape[0] == 1
        pass

    def get_primary_event(self, batch_size):
        return torch.LongTensor([[self.primary_event] * batch_size]).to(device)

    def _sample_event(self, output, greedy=True, temperature=1.0):
        if greedy:
            return output.argmax(-1)
        else:
            output = output / temperature
            probs = self.output_fc_activation(output)
            return Categorical(probs).sample()

    def init_to_hidden(self, init):
        # [batch_size, init_dim]
        batch_size = init.shape[0]
        out = self.inithid_fc(init)
        out = self.inithid_fc_activation(out)
        out = out.view(self.rnn_layers, batch_size, self.hidden_dim)
        return out

    def encoder_input(self, src, hidden, src_mask):
        """
        :param src: [batch_size, bar_len, embed_size]
        :param hidden: [batch_size, rnn_layers * hidden_dim]
        :param src_mask: [batc_size, bar_len]
        :return:
        """
        # print(f'src={src.shape}')
        # print(f'src_mask={src_mask.shape}')
        # print(f'hidden.shape={hidden.shape}')
        # sorted_length, sorted_idx = torch.sort(src_mask, descending=True)
        # print(f'sorted_length={sorted_length}')
        # _, reversed_idx = torch.sort(sorted_idx, descending=True)
        # sorted_src = src[sorted_idx]
        # sorted_src = src.index_select(0, sorted_idx)
        # packed_src = nn.utils.rnn.pack_padded_sequence(sorted_src, sorted_length, batch_first=True)
        packed_src = nn.utils.rnn.pack_padded_sequence(src, src_mask, batch_first=True, enforce_sorted=False)
        # print(f'paced_src={packed_src}')
        # packed_output, self.state = self.encoder(embedding_packed, state)  # output, (h, c)
        # outputs, inputs_size = nn.utils.rnn.pad_packed_sequence(packed_output, batch_first=True)
        packed_output, state = self.encoder(packed_src, hidden)  # output, (h, c)
        outputs, _ = nn.utils.rnn.pad_packed_sequence(packed_output, batch_first=True)

        # state.index_select(0, reversed_idx)
        # state = state[reversed_idx]
        return outputs, state

    def decoder_input(self, tar, hidden, tar_mask):
        """
        :param tar: [batch_size, bar_len, embed_size]
        :param hidden: [batch_size, rnn_layers * hidden_dim]
        :param tar_mask: [batc_size, bar_len]
        :return:
        """
        packed_tar = nn.utils.rnn.pack_padded_sequence(tar, tar_mask, batch_first=True, enforce_sorted=False)
        packed_output, state = self.decoder(packed_tar, hidden)  # output, (h, c)
        outputs, _ = nn.utils.rnn.pad_packed_sequence(packed_output, batch_first=True)
        return outputs, state

    def final_predict(self, input):
        print(f'input_shape={input.shape}')
        prob = [ layer(input) for layer in self.output_fc ]
        print(f'len_prob={len(prob)}')
        print(f'prob[0]_shape={prob[0].shape}')

        prob = torch.stack(prob, dim=-2)
        return self.output_fc_activation(prob)# [batch, mx_*, 3, event_dim]

    def Train(self, init, src, src_mask, tar, tar_mask):
        # init [batch_size, init_dim]
        hidden = self.init_to_hidden(init)
        # hidden [rnn_layers, batch_size, hidden_dim]
        # src  [batch , bar_num , bar_len * embedding]
        # src_mask  [batch , bar_num]
        # tar  [batch , bar_num', bar_len', embedding]
        # tar_mask  [batch , bar_num']
        # print(f'src.shape={src.shape}')
        # print(f'tar.shape={tar.shape}')
        src_bar_nums = src.shape[1]
        tar_bar_nums = tar.shape[1]
        bar_nums = max(src_bar_nums, tar_bar_nums)
        batch, bar_num, bar_len = tar.shape
        batch_outputs = torch.zeros((batch, bar_num, bar_len, self.embed_dim))
        for step in range(bar_nums):
            if step < src_bar_nums:
                encoder_output, encoder_hidden = self.encoder_input(src[:, step, :, :], hidden, src_mask[:, step])
                # print(f'encoder_output.shape={encoder_output.shape}')
                # print(f'encoder_hidden.shape={encoder_hidden.shape}')# (rnn_layers, batch, hidden)

            if step < tar_bar_nums:
                decoder_output, decoder_hidden = self.decoder_input(tar[:, step, :, :], encoder_hidden, tar_mask[:, step])
                # print(f'decoder_output.shape={decoder_output.shape}')
                # print(f'decoder_hidden.shape={decoder_hidden.shape}')

            if step <= src_bar_nums:
                hidden = encoder_hidden
            else:
                hidden = encoder_hidden + decoder_hidden
            # print(f'decoder_dev = {decoder_output.device}')
            res = self.final_predict(decoder_output)
            # print(res)
            print(f'res.shape={res.shape}')
            lens = res.shape[1]
            batch_outputs[:, step, :lens, :] = res
            # batch_outputs.append(res)

        return batch_outputs


    def generate(self, init, steps, events=None, greedy=1.0,
                 temperature=1.0, teacher_forcing_ratio=1.0, output_type='index', verbose=False):
        # init [batch_size, init_dim]
        # events [steps, batch_size] indeces
        # controls [1 or steps, batch_size, control_dim]

        batch_size = init.shape[0]
        assert init.shape[1] == self.init_dim
        assert steps > 0

        use_teacher_forcing = events is not None
        if use_teacher_forcing:
            assert len(events.shape) == 2
            assert events.shape[0] >= steps - 1
            events = events[:steps - 1]

        event = self.get_primary_event(batch_size)

        hidden = self.init_to_hidden(init)

        outputs = []
        step_iter = range(steps)
        if verbose:
            step_iter = Bar('Generating').iter(step_iter)

        for step in step_iter:
            output, hidden = self.gen_forward(event, hidden) #forward one step

            use_greedy = np.random.random() < greedy
            event = self._sample_event(output, greedy=use_greedy,
                                       temperature=temperature)

            if output_type == 'index':
                outputs.append(event)
            elif output_type == 'softmax':
                outputs.append(self.output_fc_activation(output))
            elif output_type == 'logit':
                outputs.append(output)
            else:
                assert False

            if use_teacher_forcing and step < steps - 1:  # avoid last one
                if np.random.random() <= teacher_forcing_ratio:
                    event = events[step].unsqueeze(0)

        return torch.cat(outputs, 0)
