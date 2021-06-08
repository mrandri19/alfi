import torch
from torch.nn import Parameter
from torch.nn.functional import relu
from gpytorch.constraints import Positive

from alfi.models import OrdinaryLFM
from alfi.configuration import VariationalConfiguration


class RNAVelocityLFM(OrdinaryLFM):
    def __init__(self,
                 num_cells, num_outputs,
                 gp_model,
                 config: VariationalConfiguration,
                 nonlinearity=relu, **kwargs):
        super().__init__(num_outputs, gp_model, config, **kwargs)
        num_genes = num_outputs // 2
        splicing_rate = 1
        transcription_rate = 1
        decay_rate = 0.4
        self.positivity = Positive()
        self.raw_splicing_rate = Parameter(self.positivity.inverse_transform(
            splicing_rate * torch.rand(torch.Size([num_genes, 1]), dtype=torch.float64)))
        self.raw_transcription_rate = Parameter(self.positivity.inverse_transform(
            transcription_rate * torch.rand(torch.Size([num_genes, 1]), dtype=torch.float64)))
        self.raw_decay_rate = Parameter(self.positivity.inverse_transform(
            decay_rate * torch.rand(torch.Size([num_genes, 1]), dtype=torch.float64)))
        self.nonlinearity = nonlinearity
        self.num_cells = num_cells
        ### Initialise random time assignments
        self.time_assignments = torch.rand(self.num_cells, requires_grad=False)

    @property
    def splicing_rate(self):
        return self.positivity.transform(self.raw_splicing_rate)

    @splicing_rate.setter
    def splicing_rate(self, value):
        self.raw_splicing_rate = self.positivity.inverse_transform(value)

    @property
    def transcription_rate(self):
        return self.positivity.transform(self.raw_transcription_rate)

    @transcription_rate.setter
    def transcription_rate(self, value):
        self.raw_transcription_rate = self.positivity.inverse_transform(value)

    @property
    def decay_rate(self):
        return self.positivity.transform(self.raw_decay_rate)

    @decay_rate.setter
    def decay_rate(self, value):
        self.raw_decay_rate = self.positivity.inverse_transform(value)

    def odefunc(self, t, h):
        """h is of shape (num_samples, num_outputs, 1)"""
        # if (self.nfe % 10) == 0:
        #     print(t)
        self.nfe += 1
        num_samples = h.shape[0]
        num_outputs = h.shape[1]
        h = h.view(num_samples, num_outputs//2, 2)
        u = h[:, :, 0].unsqueeze(-1)
        s = h[:, :, 1].unsqueeze(-1)

        f = self.f[:, :, self.t_index].unsqueeze(2)
        # print(torch.sum(s < 0), torch.sum(u < 0))
        du = self.transcription_rate * f - self.splicing_rate * u
        ds = self.splicing_rate * u - self.decay_rate * s

        h_t = torch.cat([du, ds], dim=1)

        if t > self.last_t:
            self.t_index += 1
        self.last_t = t

        return h_t

    def mix(self, f):
        """
        Parameters:
            f: (I, T)
        """
        # nn linear
        return f.repeat(1, self.num_outputs//2//10, 1)  # (S, I, t)

    def predict_f(self, t_predict, **kwargs):
        # Sample from the latent distribution
        q_f = self.get_latents(t_predict.reshape(-1))
        f = q_f.sample([500])  # (S, I, t)
        # This is a hack to wrap the latent function with the nonlinearity. Note we use the same variance.
        f = torch.mean(self.G(f), dim=0)[0]
        return torch.distributions.multivariate_normal.MultivariateNormal(f, scale_tril=q_f.scale_tril)